"""SHA-256 のハッシュ一覧を作る・照合する。標準ライブラリだけで動く。

リポジトリの根元で実行する。一覧の書式は sha256sum と同じ(「ハッシュ 2 つの空白 パス」、
パスは根元からの相対で / 区切り、並びはパスの順、改行は LF、UTF-8)。

作る:   python tools/hashes.py make -o hashes/20261114_名前.txt 提出/20261114_名前
照合:   python tools/hashes.py check hashes/20261114_名前.txt
    check は、一覧のファイルが全部あってハッシュが一致するかを見る。あわせて、
    一覧のパスに共通するフォルダの中に、一覧に無いファイルがあれば知らせる。
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path):
    return Path(os.path.relpath(path)).as_posix()


def collect(paths):
    files = set()
    for p in map(Path, paths):
        if p.is_dir():
            files.update(rel(q) for q in p.rglob("*") if q.is_file())
        elif p.is_file():
            files.add(rel(p))
        else:
            sys.exit(f"エラー: 見つからない: {p}")
    bad = [f for f in files if f.startswith("../")]
    if bad:
        sys.exit(f"エラー: リポジトリの外のファイルは入れない: {bad}")
    return sorted(files)


def make(args):
    out_rel = rel(args.output)
    lines = [f"{sha256(f)}  {f}\n" for f in collect(args.paths) if f != out_rel]
    if not lines:
        sys.exit("エラー: ファイルが 1 つも無い")
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    print(f"{len(lines)} 件を {out_rel} に書いた。一覧のハッシュ: {sha256(args.output)}")


def check(args):
    listed = {}
    with open(args.hashfile, encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            digest, sep, path = line.rstrip("\n").partition("  ")
            if not sep or len(digest) != 64:
                sys.exit(f"エラー: {args.hashfile} {n} 行目の書式が違う")
            listed[path] = digest
    ng = 0
    for path, digest in sorted(listed.items()):
        if not Path(path).is_file():
            print(f"無い      {path}")
            ng += 1
        elif sha256(path) != digest:
            print(f"不一致    {path}")
            ng += 1
        else:
            print(f"一致      {path}")
    root = os.path.commonpath(list(listed)) if len(listed) > 1 else os.path.dirname(next(iter(listed)))
    if root and Path(root).is_dir():
        for extra in sorted(set(collect([root])) - set(listed)):
            print(f"一覧に無い {extra}")
            ng += 1
    print("結果: すべて一致" if ng == 0 else f"結果: 問題 {ng} 件")
    sys.exit(0 if ng == 0 else 1)


def main():
    ap = argparse.ArgumentParser(description="SHA-256 のハッシュ一覧")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="一覧を作る")
    m.add_argument("-o", "--output", required=True)
    m.add_argument("paths", nargs="+")
    c = sub.add_parser("check", help="一覧と照合する")
    c.add_argument("hashfile")
    args = ap.parse_args()
    make(args) if args.cmd == "make" else check(args)


if __name__ == "__main__":
    main()
