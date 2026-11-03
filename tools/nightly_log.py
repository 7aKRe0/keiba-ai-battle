"""毎晩 23 時の就活用の記録の自動まとめ。

その日のコミット(このリポジトリと ~/競馬AI対決_記録/repos.txt に書いたリポジトリ)から、
まだ記録していない出来事を Haiku 5.5 が H-xx(仮説 / 検証 / 結果 / 次へ)にして記録フォルダに足す。
後から書いたものなので、見出しに「自動・仮説は後から推定」と付ける。本人は何もしなくてよい。
"""
import datetime as dt
import re
import subprocess
from pathlib import Path

import anthropic
from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = Path.home() / "競馬AI対決_記録"
JST = dt.timezone(dt.timedelta(hours=9))
MODEL = "claude-haiku-5-5"
MARK = "(23 時の自動まとめ。仮説は後から推定)"
NOTE_LIMIT = 20000


class Entry(BaseModel):
    title: str
    hypothesis: str
    test: str
    result: str
    next: str


class Entries(BaseModel):
    entries: list[Entry]


INSTRUCTIONS = """あなたは、学生の就活用ポートフォリオのための作業記録係です。
今日のコミットの一覧と、今日すでに書いた記録を読んで、まだ記録していない出来事を「仮説 → 検証 → 結果 → 次へ」の形で 0〜3 件にしてください。
- 記録に値するのは: 予想が外れた、数字が動いた、2 つ以上から選んだ、壊れて原因を見つけて直した、やめた、意見が分かれた。
- すでに記録にある出来事は書かない。細かい作業(誤字の修正、文言の直しだけ)は書かない。
- コミットとメモに書いてあることだけを使う。書いていない数字や理由を作らない。理由はメモの「理由」「本人」の行を探す。分からない所は「不明」と書く。
- 仮説は「何をすればどうなると考えたか」の 1 文にする(例: 「Haiku でルールの質問には足りる」)。決定を並べるだけにしない。
- 各項目は 1〜2 文。日本語で、である調。
- 書くことがなければ entries は空にする。"""


def repos() -> list[Path]:
    extra = LOG_DIR / "repos.txt"
    paths = [REPO]
    if extra.exists():
        paths += [Path(line.strip()) for line in extra.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [p for p in paths if (p / ".git").exists()]


def todays_commits() -> str:
    out = []
    for p in repos():
        r = subprocess.run(["git", "-C", str(p), "log", "--since=midnight", "--reverse", "--format=%h %ad %s%n%b", "--date=format:%H:%M"],
                           capture_output=True, text=True, encoding="utf-8")
        if r.stdout.strip():
            out.append(f"## リポジトリ {p.name}\n{r.stdout.strip()}")
        # 理由はコミットの文より進捗などのメモに書かれていることが多いので、今日 Markdown に足した行も渡す
        d = subprocess.run(["git", "-C", str(p), "log", "--since=midnight", "-p", "--format=", "--", "*.md"],
                           capture_output=True, text=True, encoding="utf-8")
        added = [ln[1:] for ln in d.stdout.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        if added:
            out.append(f"## リポジトリ {p.name} のメモに今日足した行(先頭 {NOTE_LIMIT} 字まで)\n" + "\n".join(added)[:NOTE_LIMIT])
    return "\n\n".join(out)


def next_number() -> int:
    index = LOG_DIR / "仮説一覧.md"
    nums = [int(n) for n in re.findall(r"^H-(\d+)", index.read_text(encoding="utf-8"), re.M)] if index.exists() else []
    return max(nums, default=0) + 1


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)


async def run(claude: anthropic.AsyncAnthropic) -> str:
    """まとめを記録フォルダに足し、#記録 に流す 1 通の文を返す。"""
    now = dt.datetime.now(JST)
    day = LOG_DIR / now.strftime("%Y-%m-%d")
    commits = todays_commits()
    written = (day / "記録.md").read_text(encoding="utf-8") if (day / "記録.md").exists() else ""
    if not commits:
        return "今日のコミットはありませんでした。自動で足した記録はありません。"
    res = await claude.messages.parse(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=INSTRUCTIONS,
        messages=[{"role": "user", "content": f"今日のコミット:\n{commits}\n\n今日すでに書いた記録:\n{written or '(なし)'}"}],
        output_format=Entries,
    )
    entries = res.parsed_output.entries if res.parsed_output else []
    if not entries:
        return "今日のコミットを見ましたが、新しく足す記録はありませんでした。"
    n = next_number()
    titles = []
    for i, e in enumerate(entries):
        e.title = re.sub(r"^H-\d+\s*", "", e.title)  # 番号はこちらで振る
        text = f"H-{n + i:02d} {e.title}{MARK}\n仮説: {e.hypothesis}\n検証: {e.test}\n結果: {e.result}\n次へ: {e.next}\n"
        append(day / "記録.md", f"## 23:00\n\n{text}\n")
        append(LOG_DIR / "仮説一覧.md", f"## {now:%Y-%m-%d} 23:00(画像は {day.name}/)\n\n{text}\n")
        titles.append(f"・H-{n + i:02d} {e.title}")
    return ("今日のコミットから、記録を自動で足しました(返事は要りません。直したいときだけ、ここに書いてください)。\n"
            + "\n".join(titles))
