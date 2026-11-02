"""採点スクリプト(ルール 5 節)。標準ライブラリだけで動く。

使い方:
    python tools/score.py 結果.csv 提出1.csv [提出2.csv ...] [--detail 明細.csv]
    python tools/score.py --format-only 提出1.csv [...]   (形式の確認だけ。結果は要らない)

提出 CSV: ファイル名 YYYYMMDD_名前.csv、UTF-8、見出し行 race_id,馬番,確率
    名前ごとに複数の開催日のファイルをまとめて採点する。
結果 CSV: 見出し行 race_id,1着,取消除外,単勝オッズ,出典(1 行 1 レース)
    1着      : 確定着順の 1 着の馬番。同着は空白区切り(例 "2 4")
    取消除外 : 出走取消・発走除外の馬番。空白区切り。無ければ空
    単勝オッズ: 出走馬(取消除外を除く)全頭の "馬番:最終単勝オッズ" を空白区切り
    出典     : JRA 公式の成績ページの URL
    採点から外すレース(レースの中止・開催の中止)は結果 CSV に載せない。

処理の順番(ルール 5 節): 取消・除外の馬を外す → CSV に無い出走馬に 0.001
    → 合計 1 に直す → 勝ち馬の p(同着は足す)→ p < 0.001 なら 0.001。
    物差し 2 つ(全馬同じ確率、1/オッズ)にも同じ処理をかける。

ブートストラップ: レース単位のペア、10,000 回。乱数は SplitMix64(種 20261114)を
    この中で実装しているので、Python の版や OS によらず同じ数字が出る。
    区間はパーセンタイル法(2.5% と 97.5%、線形補間)。
"""
import argparse
import csv
import math
import re
import sys
from pathlib import Path

FLOOR = 0.001
N_BOOT = 10_000
SEED = 20261114
RACE_ID = re.compile(r"^(\d{8})-([^-,]+)-(\d{1,2})$")
UNIFORM = "全馬同じ確率"
MARKET = "市場"


def fail(msg):
    sys.exit(f"エラー: {msg}")


def warn(msg):
    print(f"注意: {msg}", file=sys.stderr)


def parse_nums(text, where):
    out = []
    for tok in text.split():
        if not tok.isdigit() or int(tok) < 1:
            fail(f"{where}: 馬番が正の整数ではない: {tok!r}")
        out.append(int(tok))
    if len(out) != len(set(out)):
        fail(f"{where}: 馬番が重複している")
    return out


def read_results(path):
    races = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != ["race_id", "1着", "取消除外", "単勝オッズ", "出典"]:
        fail(f"{path}: 見出し行は race_id,1着,取消除外,単勝オッズ,出典")
    for i, row in enumerate(rows[1:], start=2):
        where = f"{path} {i} 行目"
        if len(row) != 5:
            fail(f"{where}: 列の数が 5 ではない")
        rid, win, scr, odds_text, _src = row
        if not RACE_ID.match(rid):
            fail(f"{where}: race_id の形式が違う: {rid!r}")
        if rid in races:
            fail(f"{where}: race_id が重複している: {rid}")
        odds = {}
        for tok in odds_text.split():
            m = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", tok)
            if not m or int(m.group(1)) < 1 or float(m.group(2)) <= 0:
                fail(f"{where}: 単勝オッズの書き方が違う: {tok!r}")
            if int(m.group(1)) in odds:
                fail(f"{where}: 単勝オッズの馬番が重複している")
            odds[int(m.group(1))] = float(m.group(2))
        winners = parse_nums(win, where)
        scratched = parse_nums(scr, where)
        if not odds or not winners:
            fail(f"{where}: 1着と単勝オッズは必須")
        if not set(winners) <= set(odds):
            fail(f"{where}: 1着の馬が単勝オッズの出走馬にいない")
        if set(scratched) & set(odds):
            fail(f"{where}: 取消除外の馬が単勝オッズの出走馬にもいる")
        races[rid] = {"winners": winners, "scratched": set(scratched), "odds": odds}
    if not races:
        fail(f"{path}: レースが 1 つも無い")
    return races


def read_submission(path):
    """{race_id: {馬番: 確率}} を返す。形式が違えば止まる。"""
    path = Path(path)
    m = re.fullmatch(r"(\d{8})_(.+)", path.stem)
    if path.suffix != ".csv" or not m:
        fail(f"{path}: ファイル名は YYYYMMDD_名前.csv")
    day, name = m.groups()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except UnicodeDecodeError:
        fail(f"{path}: UTF-8 で読めない")
    if not rows or rows[0] != ["race_id", "馬番", "確率"]:
        fail(f"{path}: 見出し行は race_id,馬番,確率")
    sub = {}
    for i, row in enumerate(rows[1:], start=2):
        where = f"{path} {i} 行目"
        if len(row) != 3:
            fail(f"{where}: 列の数が 3 ではない")
        rid, num, prob = (c.strip() for c in row)
        rm = RACE_ID.match(rid)
        if not rm:
            fail(f"{where}: race_id の形式が違う: {rid!r}")
        if rm.group(1) != day:
            fail(f"{where}: race_id の日付がファイル名の日付と違う: {rid}")
        if not num.isdigit() or int(num) < 1:
            fail(f"{where}: 馬番が正の整数ではない: {num!r}")
        try:
            p = float(prob)
        except ValueError:
            fail(f"{where}: 確率が数ではない: {prob!r}")
        if not math.isfinite(p) or p < 0:
            fail(f"{where}: 確率は 0 以上の有限の数: {prob!r}")
        horses = sub.setdefault(rid, {})
        if int(num) in horses:
            fail(f"{where}: 同じレースの同じ馬番が 2 回ある")
        horses[int(num)] = p
    return name, sub


def winner_p(probs, race, label):
    """ルール 5 節の処理の順番で、勝ち馬の確率 p を返す。"""
    starters = set(race["odds"])
    probs = {k: v for k, v in probs.items() if k not in race["scratched"]}
    extra = sorted(set(probs) - starters)
    if extra:
        warn(f"{label}: 出走馬にいない馬番を外した: {extra}")
        probs = {k: v for k, v in probs.items() if k in starters}
    for k in starters - set(probs):
        probs[k] = FLOOR
    total = math.fsum(probs.values())
    if total <= 0:
        fail(f"{label}: 確率の合計が 0 で、合計 1 に直せない(裁定が要る)")
    p = math.fsum(probs[k] for k in race["winners"]) / total
    return max(p, FLOOR)


class SplitMix64:
    def __init__(self, seed):
        self.x = seed & 0xFFFFFFFFFFFFFFFF

    def below(self, n):
        self.x = (self.x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.x
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        z ^= z >> 31
        return (z * n) >> 64


def quantile(sorted_vals, q):
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def reach(ll_model, ll_uni, ll_mkt):
    den = ll_uni - ll_mkt
    return (ll_uni - ll_model) / den if den != 0 else None


def bootstrap(losses, names):
    """losses: {系列名: [レースごとの -ln p]}。区間を dict で返す。"""
    n = len(losses[UNIFORM])
    rng = SplitMix64(SEED)
    keys = names + [UNIFORM, MARKET]
    stats = {f"reach:{nm}": [] for nm in names}
    if len(names) == 2:
        stats["diff"] = []
    skipped = 0
    for _ in range(N_BOOT):
        idx = [rng.below(n) for _ in range(n)]
        mean = {k: math.fsum(losses[k][i] for i in idx) / n for k in keys}
        if len(names) == 2:
            stats["diff"].append(mean[names[0]] - mean[names[1]])
        if mean[UNIFORM] == mean[MARKET]:
            skipped += 1
            continue
        for nm in names:
            stats[f"reach:{nm}"].append(reach(mean[nm], mean[UNIFORM], mean[MARKET]))
    out = {}
    for k, vals in stats.items():
        vals.sort()
        out[k] = (quantile(vals, 0.025), quantile(vals, 0.975)) if vals else None
    return out, skipped


def f6(x):
    return "-" if x is None else f"{x:.6f}"


def main():
    ap = argparse.ArgumentParser(description="ルール 5 節の採点")
    ap.add_argument("files", nargs="+", help="結果.csv 提出.csv ...(--format-only のときは提出だけ)")
    ap.add_argument("--detail", help="レースごとの明細を書く CSV のパス")
    ap.add_argument("--format-only", action="store_true", help="提出 CSV の形式だけを確かめる")
    args = ap.parse_args()

    if args.format_only:
        for path in args.files:
            name, sub = read_submission(path)
            print(f"形式 OK: {path}({name}、{len(sub)} レース、{sum(map(len, sub.values()))} 行)")
        return
    if len(args.files) < 2:
        fail("結果.csv と提出 CSV を 1 つ以上渡す")
    args.results, args.submissions = args.files[0], args.files[1:]
    races = read_results(args.results)
    subs = {}
    for path in args.submissions:
        name, sub = read_submission(path)
        merged = subs.setdefault(name, {})
        dup = set(merged) & set(sub)
        if dup:
            fail(f"{path}: {name} の別のファイルと race_id が重なる: {sorted(dup)}")
        merged.update(sub)
    names = sorted(subs)
    if UNIFORM in names or MARKET in names:
        fail("名前に物差しと同じ名前は使えない")

    rids = sorted(races)
    for nm in names:
        for rid in sorted(set(subs[nm]) - set(races)):
            warn(f"{nm}: 結果 CSV に無いレースは採点しない: {rid}")
        for rid in rids:
            if rid not in subs[nm]:
                warn(f"{nm}: CSV に無いレース(全馬 0.001 になり、全馬同じ確率と同じ): {rid}")

    series = names + [UNIFORM, MARKET]
    pvals = {k: [] for k in series}
    for rid in rids:
        race = races[rid]
        n = len(race["odds"])
        for nm in names:
            pvals[nm].append(winner_p(subs[nm].get(rid, {}), race, f"{nm} {rid}"))
        pvals[UNIFORM].append(winner_p({k: 1 / n for k in race["odds"]}, race, rid))
        pvals[MARKET].append(winner_p({k: 1 / o for k, o in race["odds"].items()}, race, rid))
    losses = {k: [-math.log(p) for p in v] for k, v in pvals.items()}
    ll = {k: math.fsum(v) / len(v) for k, v in losses.items()}
    ci, skipped = bootstrap(losses, names)

    print("# 採点結果\n")
    print(f"- 採点したレース数: {len(rids)}")
    print(f"- ブートストラップ: レース単位のペア、{N_BOOT} 回、SplitMix64(種 {SEED})、パーセンタイル法")
    if skipped:
        print(f"- 到達率の分母が 0 になった回は除いた: {skipped} 回")
    print("\n| 予想 | log loss | 到達率 | 到達率の 95% 区間 |")
    print("|---|---|---|---|")
    for nm in names:
        r = reach(ll[nm], ll[UNIFORM], ll[MARKET])
        c = ci[f"reach:{nm}"]
        print(f"| {nm} | {f6(ll[nm])} | {f6(r)} | {'-' if c is None else f'[{f6(c[0])}, {f6(c[1])}]'} |")
    print(f"| {UNIFORM}(物差し) | {f6(ll[UNIFORM])} | 0 | - |")
    print(f"| {MARKET}(物差し) | {f6(ll[MARKET])} | 1 | - |")
    if len(names) == 2:
        a, b = names
        d = ll[a] - ll[b]
        lo, hi = ci["diff"]
        print(f"\n## 差({a} − {b})\n")
        print(f"- 平均: {f6(d)}、95% 区間: [{f6(lo)}, {f6(hi)}]")
        win = a if d < 0 else b if d > 0 else None
        print(f"- 勝ち(log loss が小さい方): {win or '同点'}")
        if lo <= 0 <= hi:
            print("- 区間が 0 をまたぐ: 偶然と区別できない差")

    if args.detail:
        with open(args.detail, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            head = ["race_id", "出走頭数", "1着"]
            for k in series:
                head += [f"{k}_p", f"{k}_-lnp"]
            w.writerow(head)
            for i, rid in enumerate(rids):
                race = races[rid]
                row = [rid, len(race["odds"]), " ".join(map(str, race["winners"]))]
                for k in series:
                    row += [f6(pvals[k][i]), f6(losses[k][i])]
                w.writerow(row)


if __name__ == "__main__":
    main()
