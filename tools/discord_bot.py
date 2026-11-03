"""Discord の #質問 で呼ばれたら、趣旨.md と ルール.md だけを根拠に Claude Haiku 5.5 が答えるボット。

呼び方: @質問 (だれにも付けていないロールなので、通知は飛ばない)。ボット本人へのメンションでも答える。
起動: python tools/discord_bot.py (この PC で動いている間だけ答える)
鍵はリポジトリの外に置く:
  ~/.discord_keiba_token.txt   Discord のボットのトークン
  ~/.anthropic_keiba_key.txt   Claude の API キー
"""
import re
from pathlib import Path

import anthropic
import discord

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent
QA_CHANNEL_ID = 1567155228183232632  # 疑似ハッカソン_競馬AI の #質問
CALL_ROLE = "質問"
MODEL = "claude-haiku-5-5"
DOCS = ["趣旨.md", "ルール.md"]

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


def system_prompt() -> str:
    # 毎回読み直す: ルールを直して push すれば、再起動なしで答えに反映される
    docs = "\n\n".join(f"<document name=\"{n}\">\n{(REPO / n).read_text(encoding='utf-8')}\n</document>" for n in DOCS)
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
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": question}],
    )
    u = res.usage
    print(f"stop={res.stop_reason} in={u.input_tokens} cache_read={u.cache_read_input_tokens} "
          f"cache_write={u.cache_creation_input_tokens} out={u.output_tokens}")
    text = "".join(b.text for b in res.content if b.type == "text").strip()
    if res.stop_reason == "refusal" or not text:
        text = "答えを作れませんでした。言い方を変えてもう一度聞いてください。"
    return text + FOOTER


@bot.event
async def on_ready():
    print(f"ready: {bot.user}")


@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot or msg.channel.id != QA_CHANNEL_ID or not is_called(msg):
        return
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
