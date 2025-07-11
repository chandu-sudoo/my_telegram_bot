import os
import ssl
import aiohttp
import asyncio
import certifi
from aiohttp import ClientTimeout, TCPConnector
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.types import Message, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

# Tokens
BOT_TOKEN = "7811785025:AAFseQxoJfv9uVxcGO8Ic0ANg7cco4olKS4"
ASSEMBLYAI_API_KEY = "b399b171229443c68503f27aa35887e1"
OPENROUTER_API_KEY = "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"

# Bot setup
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

ssl_context = ssl.create_default_context(cafile=certifi.where())
user_transcripts = {}

async def transcribe_audio(file_path: str) -> str:
    headers = {"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/octet-stream"}
    try:
        async with aiohttp.ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
            with open(file_path, "rb") as f:
                resp = await session.post("https://api.assemblyai.com/v2/upload", data=f, headers=headers)
                resp.raise_for_status()
            audio_url = (await resp.json())["upload_url"]

        async with aiohttp.ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
            resp = await session.post("https://api.assemblyai.com/v2/transcript", json={"audio_url": audio_url}, headers=headers)
            resp.raise_for_status()
            transcript_id = (await resp.json())["id"]

        while True:
            async with aiohttp.ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
                resp = await session.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
                resp.raise_for_status()
                r = await resp.json()

            if r["status"] == "completed":
                return r["text"]
            if r["status"] == "error":
                print(f"AssemblyAI Transcription Error: {r.get('error')}")
                return f"❌ AssemblyAI Error: {r.get('error', 'Unknown transcription error')}"
            await asyncio.sleep(2)
    except aiohttp.ClientResponseError as e:
        print(f"AssemblyAI HTTP Error: {e.status} - {e.message} for URL: {e.request_info.url}")
        return f"❌ AssemblyAI HTTP Error: {e.status} - {e.message}"
    except Exception as e:
        print(f"Error during AssemblyAI transcription: {e}")
        return f"❌ Transcription failed due to an unexpected error: {e}"


async def query_openrouter(prompt: str) -> str:
    data = {
        "model": "mistralai/devstral-medium",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    timeout = ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        response_text = ""
        try:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", json=data, headers=headers) as resp:
                response_status = resp.status
                response_text = await resp.text()
                print(f"OpenRouter API Raw Response Status: {response_status}")
                print(f"OpenRouter API Raw Response Body: {response_text}")

                resp.raise_for_status()
                j = await resp.json()
                print(f"OpenRouter API Parsed Response JSON: {j}")

                if "choices" in j and j["choices"]:
                    return j["choices"][0]["message"]["content"]
                else:
                    print(f"Error: 'choices' key missing or empty in OpenRouter response: {j}")
                    return f"❌ AI request failed: Unexpected API response structure. Response: {j}"
        except aiohttp.ClientResponseError as e:
            print(f"HTTP Error from OpenRouter API: {e.status} - {e.message} for URL: {e.request_info.url}")
            if e.status == 401:
                return "❌ AI request failed: Unauthorized. Check your OpenRouter API key."
            elif e.status == 429:
                return "❌ AI request failed: Rate limited. Please try again later."
            else:
                return f"❌ AI request failed: HTTP Error {e.status} - {e.message}. Raw response: {response_text}"
        except asyncio.TimeoutError:
            print("Timeout while waiting for OpenRouter API response.")
            return "❌ AI request failed: OpenRouter API timed out."
        except Exception as e:
            print(f"An unexpected error occurred during OpenRouter API call: {e}. Raw response text: {response_text}")
            return f"❌ AI request failed: An unexpected error occurred. Error: {e}"


@dp.message(F.text == "/start")
async def cmd_start(msg: Message):
    await msg.answer("👋 Send voice / audio / video / file (≤200 MB) to transcribe, or use commands like /summarize, /translate, /ask etc. after transcription.")

@dp.message(F.text.startswith("/ask "))
async def cmd_ask(msg: Message):
    prompt = msg.text[len("/ask "):].strip()
    if not prompt:
        await msg.reply("Please provide a question after `/ask `")
        return
    await msg.reply("🤖 Thinking…")
    resp = await query_openrouter(prompt)
    await msg.reply(resp)


@dp.message(F.text == "/trans")
async def cmd_trans(msg: Message):
    if not msg.reply_to_message:
        await msg.reply("❗ Please reply to a voice/audio/video/document message with /trans to transcribe it.")
        return

    file_msg = msg.reply_to_message
    media = file_msg.voice or file_msg.audio or file_msg.video or file_msg.document
    if not media:
        await msg.reply("❌ Replied message must contain a transcribable file (audio/video/document/voice).")
        return

    await msg.reply("⏳ Transcribing your media...")
    try:
        info = await bot.get_file(media.file_id)
        ext = os.path.splitext(media.file_name or "")[-1] or ".bin"
        input_file = f"input_trans_{msg.from_user.id}_{media.file_unique_id}{ext}"
        await bot.download_file(info.file_path, input_file)

        text = await transcribe_audio(input_file)
        user_transcripts[msg.from_user.id] = text

        if len(text) < 4000:
            await msg.reply(f"📝 Transcription:\n{text}")
        else:
            output_file = f"result_trans_{msg.from_user.id}_{media.file_unique_id}.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(text)
            await msg.reply_document(FSInputFile(output_file))
            os.remove(output_file)
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")
    finally:
        if os.path.exists(input_file):
            os.remove(input_file)


async def handle_ai_command(msg: Message, mode: str):
    user_id = msg.from_user.id
    text = user_transcripts.get(user_id)

    if not text:
        await msg.reply("⚠️ No transcript found. Please transcribe audio/media first (reply with /trans).")
        return

    prompt_map = {
        "summarize": f"Summarize the following text:\n\n{text}",
        "translate": f"Translate this to Hindi:\n\n{text}",
        "sentiment": f"Analyze the sentiment of this text (Positive/Negative/Neutral):\n\n{text}",
        "chat": f"The user just said:\n\n{text}\n\nReply naturally as if you're continuing the conversation.",
        "grammarcheck": f"Check grammar and suggest corrections:\n\n{text}",
        "rephrase": f"Rephrase this to be clearer and more fluent:\n\n{text}",
        "keywords": f"Extract important keywords or phrases from this text:\n\n{text}",
        "evaluate": f"You're an English teacher. Evaluate this student's answer. Score out of 10 based on grammar, coherence, and relevance. Then explain your reasoning:\n\n{text}",
        "feedback": f"Give improvement suggestions to the student based on this answer:\n\n{text}",
        "questions": f"Generate 5 comprehension questions based on this text:\n\n{text}"
    }

    if mode not in prompt_map:
        await msg.reply("❓ Unknown AI command.")
        return

    await msg.reply(f"🤖 Processing your request ({mode})...")
    response = await query_openrouter(prompt_map[mode])
    await msg.reply(response)


@dp.message(F.text.in_({
    "/summarize", "/translate", "/sentiment", "/chat",
    "/grammarcheck", "/rephrase", "/keywords",
    "/evaluate", "/feedback", "/questions"
}))
async def command_router(msg: Message):
    cmd = msg.text[1:]
    await handle_ai_command(msg, cmd)


async def main():
    print("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
