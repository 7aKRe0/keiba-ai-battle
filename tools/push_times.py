"""GitHub に記録された push の時刻を `gh api` で取って、`記録/` に書き足す。

コミットの時刻は自己申告(手元の時計)なので使わない。使うのは、GitHub の
リポジトリのアクティビティ(GET /repos/{owner}/{repo}/activity)の timestamp と actor。
gh にログインしてから、リポジトリの根元で実行する。

記録: python tools/push_times.py log
    push と force push を全部取り、記録/push時刻.csv にまだ無いものだけを書き足す。
提出: python tools/push_times.py submission --deadline "2026-11-13 21:00" --file hashes/20261114_甲.txt
    締切(日本時間)より前の push を時刻の順に並べ、そのファイルを変えた push と、その actor を出す。
    締切前の最後の push の時点のファイルを正式な提出とし、その SHA-256 を出す。
    結果を 記録/提出の確認.md に書き足す。
"""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
LOG = Path("記録/push時刻.csv")
SUBMISSION_LOG = Path("記録/提出の確認.md")
COLUMNS = ["記録した時刻_JST", "activity_id", "push時刻_UTC", "push時刻_JST",
           "種類", "actor", "ref", "before", "after"]
ZERO = "0" * 40


def gh(*args, raw=False):
    r = subprocess.run(["gh", "api", *args], capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")
        if "404" in err or "Not Found" in err:
            return None  # 無い(ファイルやコミットが存在しない)
        sys.exit(f"エラー: gh api {' '.join(args)} が失敗した(認証・通信・制限など)。判定を中止する: {err.strip()}")
    return r.stdout if raw else r.stdout.decode("utf-8")


def repo_name():
    url = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else sys.exit("エラー: origin から owner/repo を読めない。--repo で渡す")


def activities(repo):
    out = gh("--paginate", f"repos/{repo}/activity?per_page=100", "--jq", ".[]")
    if out is None:
        sys.exit("エラー: gh api でアクティビティを取れなかった(gh auth status を確かめる)")
    items = [json.loads(line) for line in out.splitlines() if line.strip()]
    items = [a for a in items if a.get("activity_type") in ("push", "force_push")]
    return sorted(items, key=lambda a: (a["timestamp"], a["id"]))


def utc(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def jst(ts):
    return utc(ts).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def cmd_log(args):
    seen = set()
    if LOG.exists():
        with open(LOG, encoding="utf-8", newline="") as f:
            seen = {row["activity_id"] for row in csv.DictReader(f)}
    new = [a for a in activities(args.repo) if str(a["id"]) not in seen]
    first = not LOG.exists()
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if first:
            w.writerow(COLUMNS)
        stamp = now_jst()
        for a in new:
            w.writerow([stamp, a["id"], a["timestamp"], jst(a["timestamp"]), a["activity_type"],
                        (a.get("actor") or {}).get("login", ""), a["ref"], a["before"], a["after"]])
    print(f"{len(new)} 件を {LOG} に書き足した")


def blob_sha(repo, path, ref):
    if ref == ZERO:
        return None
    out = gh(f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}", "--jq", ".sha")
    return out.strip() if out else None


def cmd_submission(args):
    deadline = datetime.strptime(args.deadline, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    ref = f"refs/heads/{args.branch}"
    pushes = [a for a in activities(args.repo) if a["ref"] == ref and utc(a["timestamp"]) < deadline]
    lines = [f"\n## 提出の確認 {args.file}(締切 {args.deadline} 日本時間、記録 {now_jst()})\n",
             f"- ブランチ {args.branch} への締切前の push: {len(pushes)} 件"]
    for a in pushes:
        before, after = blob_sha(args.repo, args.file, a["before"]), blob_sha(args.repo, args.file, a["after"])
        if before != after:
            what = "作った" if before is None else "消した" if after is None else "変えた"
            lines.append(f"- {jst(a['timestamp'])} {a['activity_type']} actor={a['actor']['login']}"
                         f" after={a['after'][:12]}: ファイルを{what}")
    last = pushes[-1] if pushes else None
    body = None
    if last:
        body = gh("-H", "Accept: application/vnd.github.raw",
                  f"repos/{args.repo}/contents/{urllib.parse.quote(args.file)}?ref={last['after']}", raw=True)
    if body is None:
        lines.append("- 結果: 締切前の最後の push の時点で、このファイルは無い(提出なし)")
    else:
        lines.append(f"- 結果: 締切前の最後の push は {jst(last['timestamp'])}(after={last['after']})。"
                     f"その時点のファイルの SHA-256 = {hashlib.sha256(body).hexdigest()}")
    text = "\n".join(lines) + "\n"
    print(text)
    with open(SUBMISSION_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description="GitHub に記録された push の時刻")
    ap.add_argument("--repo", help="owner/repo(省略時は origin から読む)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("log")
    s = sub.add_parser("submission")
    s.add_argument("--deadline", required=True, help='日本時間 "YYYY-MM-DD HH:MM"')
    s.add_argument("--file", required=True)
    s.add_argument("--branch", default="main")
    args = ap.parse_args()
    args.repo = args.repo or repo_name()
    cmd_log(args) if args.cmd == "log" else cmd_submission(args)


if __name__ == "__main__":
    main()
