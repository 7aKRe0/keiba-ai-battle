"""作り直しの確認(ルール 4 節): 提出 CSV と作り直した CSV を比べる。

    python tools/compare.py 提出.csv 作り直し.csv

同じ (race_id, 馬番) の組がそろい、全馬の確率の差が 0.000001 以内なら「一致」。
"""
import csv
import sys

TOL = 0.000001


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != ["race_id", "馬番", "確率"]:
        sys.exit(f"エラー: {path}: 見出し行は race_id,馬番,確率")
    return {(r[0].strip(), int(r[1])): float(r[2]) for r in rows[1:]}


a, b = load(sys.argv[1]), load(sys.argv[2])
only = sorted(set(a) ^ set(b))
diff = max((abs(a[k] - b[k]), k) for k in set(a) & set(b)) if set(a) & set(b) else (0.0, None)
print(f"片方にしか無い行: {len(only)} 件 {only[:5]}")
print(f"確率の差の最大: {diff[0]:.10f} {diff[1]}")
ok = not only and diff[0] <= TOL
print("結果: 一致" if ok else "結果: 不一致")
sys.exit(0 if ok else 1)
