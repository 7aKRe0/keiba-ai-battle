"""score.py の出力が tests/手計算.md の手計算と一致するかを確かめる。

    python tests/test_score.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "tests" / "data"
CMD = [sys.executable, str(ROOT / "tools" / "score.py"), str(D / "結果.csv"),
       str(D / "20261114_A.csv"), str(D / "20261114_B.csv")]

# 手計算.md の値(小数 6 桁)
EXPECTED = {
    "A": ("2.761193", "-4.866312"),
    "B": ("0.828635", "0.912279"),
    "全馬同じ確率(物差し)": ("1.133732", "0"),
    "市場(物差し)": ("0.799298", "1"),
}
EXPECTED_DIFF = "1.932557"


def run():
    r = subprocess.run(CMD, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


out = run()
for name, (ll, rate) in EXPECTED.items():
    m = re.search(rf"^\| {re.escape(name)} \| (\S+) \| (\S+) \|", out, re.M)
    assert m, f"{name} の行が無い"
    assert m.groups() == (ll, rate), f"{name}: 出力 {m.groups()} / 手計算 {(ll, rate)}"
assert f"- 平均: {EXPECTED_DIFF}、" in out, "差が手計算と違う"
assert "- 勝ち(log loss が小さい方): B" in out
assert run() == out, "2 回目の出力が 1 回目と違う"
print("OK: 手計算と一致し、2 回実行して同じ出力")
