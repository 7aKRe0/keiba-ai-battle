"""OpenTimestamps の stamp・upgrade・verify をまとめる。

opentimestamps-client(LGPL v3)をそのまま呼ぶだけで、そのコードは写していない。
    pip install opentimestamps-client

stamp:   python tools/ots.py stamp hashes/20261114_甲.txt hashes/20261114_乙.txt
         (1 回の呼び出しで、その日の 2 人のファイルにまとめて時刻証明を付ける。
          ファイルごとに .ots ができる)
upgrade: python tools/ots.py upgrade hashes/*.ots
         (stamp の直後は「受付済み」だけ。Bitcoin のブロックに入るまで数時間かかる。
          入った後に upgrade すると、カレンダーに頼らず確かめられる形になる)
verify:  python tools/ots.py verify hashes/20261114_甲.txt.ots [...]
         Bitcoin のノードを使わずに確かめる。手順は 2 段:
         1. ots --no-bitcoin verify で、ファイル → .ots の道筋 → ブロック N の merkle root
            を計算する(ファイルが 1 バイトでも違えば、ここで不一致になる)。
         2. ブロック N の merkle root を、2 つのブロックエクスプローラ(blockstream.info と
            mempool.space)から取って比べる。2 つとも一致すれば「一致」。
         ノードがあるなら、代わりに `ots verify -f 元のファイル 元のファイル.ots` でよい。
"""
import glob
import json
import re
import subprocess
import sys
import urllib.request

# Windows では python-bitcoinlib が OpenSSL を ctypes.util.find_library('ssl') で探して None になり落ちる。
# Python に付いている libcrypto-3.dll を返すようにしてから読み込む(2026-11-02 Windows で確認)。
OTS = [sys.executable, "-c",
       "import ctypes.util, os, sys; f = ctypes.util.find_library; "
       "dll = os.path.join(sys.base_prefix, 'DLLs', 'libcrypto-3.dll'); "
       "ctypes.util.find_library = lambda n: f(n) or (dll if n in ('ssl', 'ssl.35', 'libeay32') else None); "
       "from otsclient.ots import main; sys.argv[0] = 'ots'; sys.exit(main())"]
EXPLORERS = ["https://blockstream.info/api", "https://mempool.space/api"]


def ots(*args):
    r = subprocess.run(OTS + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as res:
        return res.read().decode()


def merkle_root(api, height):
    block_hash = fetch(f"{api}/block-height/{height}").strip()
    return json.loads(fetch(f"{api}/block/{block_hash}"))["merkle_root"]


def verify(ots_path):
    target = ots_path[:-4] if ots_path.endswith(".ots") else sys.exit(f"エラー: .ots ではない: {ots_path}")
    code, out = ots("--no-bitcoin", "verify", "-f", target, ots_path)
    print(out.strip())
    m = re.search(r"Bitcoin block (\d+) has merkleroot ([0-9a-f]{64})", out)
    if not m:
        print(f"結果: {ots_path}: Bitcoin の証明がまだ無い(upgrade 待ち)か、ファイルが一致しない")
        return False
    height, root = int(m.group(1)), m.group(2)
    ok = 0
    for api in EXPLORERS:
        try:
            got = merkle_root(api, height)
        except Exception as e:  # ネットワークの失敗は「確かめられなかった」として扱う
            print(f"  {api}: 取れなかった({e})")
            continue
        print(f"  {api}: ブロック {height} の merkle root = {got} → {'一致' if got == root else '不一致'}")
        if got != root:
            print(f"結果: {ots_path}: 不一致")
            return False
        ok += 1
    if ok < len(EXPLORERS):
        print(f"結果: {ots_path}: 届いたエクスプローラが {ok}/{len(EXPLORERS)} か所で未確認。"
              f"ブロック {height} の merkle root が {root} かを、別の手段で確かめる")
        return False
    print(f"結果: {ots_path}: 一致(ブロック {height}、エクスプローラ {ok} か所)")
    return True


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("stamp", "upgrade", "verify"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
    # Windows の PowerShell は *.ots を展開しないので、ここで展開する
    files = [g for a in sys.argv[2:] for g in (sorted(glob.glob(a)) if glob.has_magic(a) else [a])]
    if not files:
        sys.exit("エラー: 対象のファイルが 1 つも無い")
    if cmd == "verify":
        results = [verify(f) for f in files]
        sys.exit(0 if all(results) else 1)
    code, out = ots(cmd, *files)
    print(out.strip())
    sys.exit(code)


if __name__ == "__main__":
    main()
