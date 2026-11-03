"""ルールのフォーラムで決まったことを、ルール.md・趣旨.md の書き換えの下書きにして、承認されたら push する。

下書きは Claude Sonnet 5.5 が作る。書き換えるのは、主催が下書きに ✅ を付けたときだけ(discord_bot.py)。
"""
import datetime as dt
import subprocess
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent
MODEL = "claude-sonnet-5-5"
FILES = ["ルール.md", "趣旨.md"]
JST = dt.timezone(dt.timedelta(hours=9))


class Edit(BaseModel):
    file: Literal["ルール.md", "趣旨.md"]
    before: str  # 今のファイルからそのまま写した、ちょうど 1 か所に当たる文
    after: str


class Draft(BaseModel):
    decided: str  # 決まったこと。だれが何に賛成したか
    open_points: str  # まだ決まっていないこと。なければ空
    edits: list[Edit]
    history: str  # ルール.md の変更履歴に足す 1 行(先頭の「- 」と日付は付けない)


INSTRUCTIONS = """あなたは競馬AI対決(参加者 5 人の個人ハッカソン)の、ルールの書き換えの下書き係です。
Discord のスレッドの話し合いを読んで、決まったことだけを、ルール.md・趣旨.md の書き換えにしてください。

- decided: 決まったことを短く。だれが何に賛成・反対したかを名前つきで書く。投票の結果があれば書く。
- open_points: まだ決まっていないこと、意見が分かれたままのこと。なければ空。
- edits: 書き換え。決まっていないことは書き換えない。決まったことに関係しない文は直さない。
  - before は、今のファイルの文をそのまま写す(1 文字も変えない)。ファイルの中でちょうど 1 か所に当たる長さにする。
  - 文を足すときは、足す場所の直前の行を before に入れ、after でその行の後ろに足す。
  - 書き方は今のルール.md に合わせる(である調、短い文、1 行に 1 つのこと)。
  - 変更履歴の節には edits で触らない。
- history: 変更履歴に足す 1 行。「第 N 版(参加者の合意): 何をどう変えたか」の形。N は今の最後の版の次。
- 話し合いに無いことを足さない。読み取れないことは open_points に書く。"""


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


async def make_draft(claude: anthropic.AsyncAnthropic, title: str, transcript: str) -> Draft:
    _git("pull", "--ff-only", "-q")
    docs = "\n\n".join(f"<document name=\"{n}\">\n{(REPO / n).read_text(encoding='utf-8')}\n</document>" for n in FILES)
    res = await claude.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=[{"type": "text", "text": f"{INSTRUCTIONS}\n\n{docs}", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"スレッド「{title}」の話し合い:\n\n{transcript}"}],
        output_format=Draft,
    )
    u = res.usage
    print(f"draft stop={res.stop_reason} in={u.input_tokens} cache_read={u.cache_read_input_tokens} out={u.output_tokens}")
    if res.parsed_output is None:
        raise RuntimeError(f"下書きを作れなかった(stop={res.stop_reason})")
    return res.parsed_output


def apply_draft(draft: Draft, title: str, url: str) -> str:
    """書き換えてコミットし、push する。コミットのハッシュを返す。当たらない文があれば何も変えずに止まる。"""
    _git("pull", "--ff-only", "-q")
    texts = {n: (REPO / n).read_text(encoding="utf-8") for n in FILES}
    for e in draft.edits:
        n = texts[e.file].count(e.before)
        if n != 1:
            raise ValueError(f"{e.file} に「{e.before[:40]}…」が {n} か所ある(1 か所でないと書き換えない)")
        texts[e.file] = texts[e.file].replace(e.before, e.after)
    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    texts["ルール.md"] = texts["ルール.md"].rstrip("\n") + f"\n- {today} {draft.history}(Discord: {url})\n"
    changed = [n for n in FILES if texts[n] != (REPO / n).read_text(encoding="utf-8")]
    for n in changed:
        (REPO / n).write_text(texts[n], encoding="utf-8", newline="\n")
    _git("add", "--", *changed)
    _git("commit", "-q", "-m", f"rule: {title}(Discord で決定、主催が承認)\n\n{url}", "--", *changed)
    _git("push", "-q")
    return _git("rev-parse", "--short", "HEAD")


def render(draft: Draft) -> str:
    lines = ["**下書き(まだルールは変わっていません)**", "", "**決まったこと**", draft.decided]
    if draft.open_points.strip():
        lines += ["", "**まだ決まっていないこと**", draft.open_points]
    lines += ["", "**書き換え**"]
    for e in draft.edits:
        lines += [f"`{e.file}`", "```diff", *[f"- {s}" for s in e.before.splitlines()], *[f"+ {s}" for s in e.after.splitlines()], "```"]
    if not draft.edits:
        lines.append("(なし)")
    lines += ["", f"**変更履歴に足す 1 行**", draft.history]
    return "\n".join(lines)
