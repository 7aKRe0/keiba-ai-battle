"""競馬AI対決の Discord ボット。

1. #質問 で呼ばれたら、趣旨.md と ルール.md だけを根拠に Claude Haiku 5.5 が答える。
   呼び方: @質問 (だれにも付けていないロールなので、通知は飛ばない)。ボット本人へのメンションでも答える。
   ほかのチャンネルでは反応しない。
2. #記録 (サーバーの持ち主だけが見える)の持ち主の投稿と画像を、リポジトリの外の
   ~/競馬AI対決_記録/ に日付ごとに保存する。H-xx で始まる投稿は 仮説一覧.md にも足す。
   毎晩 23 時に #記録 で 1 回だけ「今日の記録は?」と聞く。
3. 起動したとき、止まっていた間の #質問(返信のない呼び出し)と #記録(✅ のない投稿)を処理する。
4. ルールのフォーラムでタグを「決定」にするか、スレッドで @まとめ と書くと、Sonnet 5.5 がスレッドを読んで
   ルール.md・趣旨.md の書き換えの下書きを出す。サーバーの持ち主が下書きに ✅ を付けたら、書き換えて push し、
   #お知らせ に流す(tools/rule_drafts.py)。
趣旨.md・ルール.md は GitHub の main から読む(push すれば答えに反映。読めないときはこの PC のファイル)。
起動: python tools/discord_bot.py (この PC で動いている間だけ動く)
鍵はリポジトリの外に置く:
  ~/.discord_keiba_token.txt   Discord のボットのトークン
  ~/.anthropic_keiba_key.txt   Claude の API キー
"""
import asyncio
import datetime as dt
import io
import re
from pathlib import Path

from urllib.parse import quote

import aiohttp
import anthropic
import discord
from discord.ext import tasks

import rule_drafts

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent
QA_CHANNEL_ID = 1567155228183232632  # 疑似ハッカソン_競馬AI の #質問
LOG_CHANNEL_ID = 1567163631890866336  # 同じく #記録
FORUM_ID = 1567155224467210303  # 同じく ルール(フォーラム)
NEWS_ID = 1567155217844277359  # 同じく #お知らせ
DRAFT_ROLE = "まとめ"
DECIDED_TAG = "決定"
DRAFT_FILE = "draft.json"
LOG_DIR = HOME / "競馬AI対決_記録"
JST = dt.timezone(dt.timedelta(hours=9))
NIGHTLY_PROMPT = ("今日、予想と違ったこと・数字が動いたこと・2 つから選んだこと・壊れて直したこと・やめたこと・"
                  "意見が分かれたことはありましたか? あれば H-xx で書いてください(仮説 / 検証 / 結果 / 次へ)。")
CALL_ROLE = "質問"
MODEL = "claude-haiku-5-5"
DOCS = ["趣旨.md", "ルール.md"]
RAW = "https://raw.githubusercontent.com/7aKRe0/keiba-ai-battle/main/"
CATCH_UP = 100  # 起動時に見直す、各チャンネルの最新の件数

INSTRUCTIONS = """あなたは競馬AI対決(参加者 5 人の個人ハッカソン)の Discord で、ルールの質問に答える係です。
下の文書だけを根拠に、日本語で短く答えてください。
- 文書に書いてあることだけを答える。推測や一般論で補わない。
- 根拠の節(例: ルール 4 節)を答えに添える。
- 文書に書かれていないことは「ルール・趣旨には書かれていません。#質問 で主催に確認してください」と答える。
- 予想のやり方やモデルの作り方の助言はしない。
- 1500 字以内。"""

FOOTER = "\n-# ボットの答えは裁定ではありません。根拠は趣旨.md・ルール.md です。"


def read_secret(name: str) -> str:
    return (HOME / name).read_text(encoding="utf-8").strip()


async def read_doc(name: str) -> str:
    # 毎回読み直す: ルールを直して push すれば、再起動なしで答えに反映される
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s, s.get(RAW + quote(name)) as r:
            r.raise_for_status()
            return await r.text(encoding="utf-8")
    except (aiohttp.ClientError, TimeoutError) as e:
        print(f"GitHub から読めない({name}): {e!r}。この PC のファイルを使う")
        return (REPO / name).read_text(encoding="utf-8")


async def system_prompt() -> str:
    docs = "\n\n".join([f"<document name=\"{n}\">\n{await read_doc(n)}\n</document>" for n in DOCS])
    return f"{INSTRUCTIONS}\n\n{docs}"


claude = anthropic.AsyncAnthropic(api_key=read_secret(".anthropic_keiba_key.txt"))
# ロールへのメンションでは本文が届かないので Message Content Intent を使う
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


def is_called(msg: discord.Message) -> bool:
    # @質問、ボット本人、ボットと同じ名前のロールのどれでも呼べる
    me = msg.guild.me
    return me in msg.mentions or any(r.name == CALL_ROLE or r in me.roles for r in msg.role_mentions)


async def answer(question: str) -> str:
    res = await claude.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=[{"type": "text", "text": await system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": question}],
    )
    u = res.usage
    print(f"stop={res.stop_reason} in={u.input_tokens} cache_read={u.cache_read_input_tokens} "
          f"cache_write={u.cache_creation_input_tokens} out={u.output_tokens}")
    text = "".join(b.text for b in res.content if b.type == "text").strip()
    if res.stop_reason == "refusal" or not text:
        text = "答えを作れませんでした。言い方を変えてもう一度聞いてください。"
    return text + FOOTER


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)


async def save_log(msg: discord.Message) -> None:
    t = msg.created_at.astimezone(JST)
    day = LOG_DIR / t.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    files = []
    for a in msg.attachments:
        name = f"{t:%H%M%S}_{a.filename}"
        await a.save(day / name)
        files.append(name)
    entry = f"## {t:%H:%M}\n\n{msg.content}\n" + "".join(f"\n![]({n})\n" for n in files) + "\n"
    append(day / "記録.md", entry)
    if re.match(r"H-\d+", msg.content):
        append(LOG_DIR / "仮説一覧.md", f"## {t:%Y-%m-%d %H:%M}(画像は {day.name}/)\n\n{msg.content}\n\n")
    await msg.add_reaction("✅")


@tasks.loop(time=dt.time(23, 0, tzinfo=JST))
async def nightly():
    await bot.get_channel(LOG_CHANNEL_ID).send(NIGHTLY_PROMPT)


async def catch_up() -> None:
    """止まっていた間の取りこぼしを、Discord に残っている履歴から拾う。"""
    qa = [m async for m in bot.get_channel(QA_CHANNEL_ID).history(limit=CATCH_UP)]
    replied = {m.reference.message_id for m in qa if m.author == bot.user and m.reference}
    for m in reversed(qa):  # 古い順
        if not m.author.bot and is_called(m) and m.id not in replied:
            print(f"取りこぼしの質問に答える: {m.id}")
            await handle_question(m)
    async for m in bot.get_channel(LOG_CHANNEL_ID).history(limit=CATCH_UP, oldest_first=True):
        if m.author.id == m.guild.owner_id and not any(r.emoji == "✅" and r.me for r in m.reactions):
            print(f"取りこぼしの記録を保存する: {m.id}")
            await save_log(m)


@bot.event
async def on_ready():
    if not nightly.is_running():
        nightly.start()
    print(f"ready: {bot.user}")
    await catch_up()


@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return
    if msg.channel.id == LOG_CHANNEL_ID and msg.author.id == msg.guild.owner_id:
        await save_log(msg)
    elif msg.channel.id == QA_CHANNEL_ID and is_called(msg):
        await handle_question(msg)
    elif getattr(msg.channel, "parent_id", None) == FORUM_ID and any(r.name == DRAFT_ROLE for r in msg.role_mentions):
        await draft_thread(msg.channel)


async def send_long(channel, text: str, **kw) -> discord.Message:
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for c in chunks[:-1]:
        await channel.send(c)
    return await channel.send(chunks[-1], **kw)


async def draft_thread(thread: discord.Thread) -> None:
    """スレッドの話し合いから、ルールの書き換えの下書きを出す。"""
    lines = []
    async for m in thread.history(limit=None, oldest_first=True):
        if m.author.bot and m.id != thread.id:  # ボットの発言は最初の投稿(今・案・決めること)だけ読む
            continue
        lines.append(f"{m.author.display_name}: {m.content}")
    await thread.send("下書きを作っています(1 分ほどかかります)。")
    try:
        draft = await rule_drafts.make_draft(claude, thread.name, "\n\n".join(lines))
    except Exception as e:  # noqa: BLE001 ボットを止めずにスレッドへ知らせる
        print(f"draft error: {e!r}")
        await thread.send(f"下書きを作れませんでした: {e}")
        return
    body = rule_drafts.render(draft) + "\n\n**主催がこのメッセージに ✅ を付けると、ルールに反映して push します。**直したいときは、スレッドに書いてから `@まとめ` で出し直してください。"
    file = discord.File(io.BytesIO(draft.model_dump_json().encode("utf-8")), filename=DRAFT_FILE)
    await send_long(thread, body, file=file)


@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    was = any(t.name == DECIDED_TAG for t in before.applied_tags)
    now = any(t.name == DECIDED_TAG for t in after.applied_tags)
    if after.parent_id == FORUM_ID and now and not was:
        await draft_thread(after)


@bot.event
async def on_raw_reaction_add(p: discord.RawReactionActionEvent):
    guild = bot.get_guild(p.guild_id) if p.guild_id else None
    if str(p.emoji) != "✅" or guild is None or p.user_id != guild.owner_id:
        return
    thread = guild.get_thread(p.channel_id) or await bot.fetch_channel(p.channel_id)
    if getattr(thread, "parent_id", None) != FORUM_ID:
        return
    msg = await thread.fetch_message(p.message_id)
    att = next((a for a in msg.attachments if a.filename == DRAFT_FILE), None)
    if msg.author != bot.user or att is None or any(r.emoji == "✅" and r.me for r in msg.reactions):
        return  # 下書きでない、または反映済み
    draft = rule_drafts.Draft.model_validate_json(await att.read())
    try:
        sha = await asyncio.to_thread(rule_drafts.apply_draft, draft, thread.name, thread.jump_url)
    except Exception as e:  # noqa: BLE001
        print(f"apply error: {e!r}")
        await thread.send(f"反映できませんでした(ルールは変えていません): {e}")
        return
    await msg.add_reaction("✅")
    link = f"https://github.com/7aKRe0/keiba-ai-battle/commit/{sha}"
    await thread.send(f"ルールに反映して push しました: {link}")
    await bot.get_channel(NEWS_ID).send(f"ルールを変えました: 「{thread.name}」\n{thread.jump_url}\n{link}")


async def handle_question(msg: discord.Message) -> None:
    question = re.sub(r"<@[!&]?\d+>", "", msg.content).strip()
    if not question:
        await msg.reply("質問を書いて、もう一度 @質問 を付けて送ってください。")
        return
    async with msg.channel.typing():
        try:
            reply = await answer(question)
        except anthropic.APIError as e:
            print(f"error: {e!r}")
            reply = "Claude の呼び出しに失敗しました。主催に知らせてください。"
    for i in range(0, len(reply), 1900):  # Discord は 1 通 2000 字まで
        await msg.reply(reply[i:i + 1900], mention_author=False)


if __name__ == "__main__":
    bot.run(read_secret(".discord_keiba_token.txt"))
