import os
import json
import random
import string
import asyncio
from fastapi import FastAPI, Request
import aiohttp
from dotenv import load_dotenv
import uvicorn

load_dotenv()

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not WEBSHARE_API_KEY or not WEBHOOK_URL:
    raise ValueError("Missing required environment variables.")

BOT_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CHECKER_RUNNING = False
PROXIES = []
PROXIES_FILE = "proxies.txt"
CONTROLLER_MESSAGE_ID = None

# Small sample pronounceable 4-letter usernames, you can expand this list
PRONOUNCEABLE_4L = [
    "tsla", "nexo", "vibe", "mobi", "zelo", "riva", "fino", "cora",
    "luma", "koda", "belo", "sora", "telo", "pavo", "runo", "jexo"
]

async def set_telegram_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={WEBHOOK_URL}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            result = await resp.json()
            print(f"Set webhook result: {result}")

def load_cached_proxies():
    global PROXIES
    if os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        PROXIES = lines[:100]
        print(f"Loaded {len(PROXIES)} cached proxies.")
    else:
        PROXIES = []
        print("No cached proxies file found.")

async def validate_proxy(proxy):
    try:
        timeout = aiohttp.ClientTimeout(total=7)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://www.tiktok.com", proxy=proxy) as r:
                return r.status == 200
    except:
        return False

async def refresh_proxies():
    global PROXIES
    print("Refreshing proxies from Webshare...")
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=100&page=1"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                proxies_raw = data.get("results", [])

        proxies_raw = proxies_raw[:100]

        raw_proxies = [
            f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in proxies_raw
        ]

        valid_proxies = []
        semaphore = asyncio.Semaphore(20)

        async def validate_and_collect(proxy):
            nonlocal valid_proxies
            async with semaphore:
                if len(valid_proxies) >= 100:
                    return
                if await validate_proxy(proxy):
                    valid_proxies.append(proxy)

        await asyncio.gather(*[validate_and_collect(p) for p in raw_proxies])

        PROXIES = valid_proxies[:100]

        with open(PROXIES_FILE, "w") as f:
            for proxy in PROXIES:
                f.write(proxy + "\n")

        print(f"Validated and saved {len(PROXIES)} proxies.")

    except Exception as e:
        print(f"Error refreshing proxies: {e}")

async def send_message(text, buttons=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    async with aiohttp.ClientSession() as session:
        await session.post(f"{BOT_API_URL}/sendMessage", json=payload)

async def edit_message(message_id, text, buttons=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    async with aiohttp.ClientSession() as session:
        await session.post(f"{BOT_API_URL}/editMessageText", json=payload)

async def send_available_username(username):
    buttons = [[{"text": "Claim", "url": f"https://www.tiktok.com/@{username}"}]]
    await send_message(f"<b>@{username}</b> is available!", buttons)

async def check_username(session, username, proxy):
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)"
        ])
    }
    try:
        async with session.get(url, proxy=proxy, headers=headers, timeout=10) as resp:
            return resp.status == 404
    except:
        return None

async def run_checker_loop():
    global CHECKER_RUNNING
    CHECKER_RUNNING = True
    proxy_pool = PROXIES.copy()
    proxy_index = 0
    usernames = PRONOUNCEABLE_4L.copy()

    async with aiohttp.ClientSession() as session:
        while CHECKER_RUNNING:
            if not usernames:
                # regenerate or refill list if empty
                usernames = PRONOUNCEABLE_4L.copy()

            username = usernames.pop(0)

            if not proxy_pool:
                await send_message("No proxies left, refreshing...")
                await refresh_proxies()
                proxy_pool = PROXIES.copy()
                if not proxy_pool:
                    await send_message("No valid proxies available, stopping checker.")
                    CHECKER_RUNNING = False
                    break

            proxy = proxy_pool[proxy_index % len(proxy_pool)]
            result = await check_username(session, username, proxy)

            if result is True:
                await send_available_username(username)
            elif result is None:
                # Proxy might be dead, remove it
                proxy_pool.remove(proxy)

            proxy_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.0))

@app.post("/webhook")
async def telegram_webhook(request: Request):
    global CHECKER_RUNNING, CONTROLLER_MESSAGE_ID
    data = await request.json()

    message = data.get("message", {})
    callback = data.get("callback_query", {})

    if "text" in message:
        text = message["text"]
        if text == "/start":
            buttons = [[
                {"text": "Start", "callback_data": "start"},
                {"text": "Stop", "callback_data": "stop"},
                {"text": "Refresh Proxies", "callback_data": "refresh"}
            ]]
            await send_message("Checker Controls:", buttons)
        return {"ok": True}

    if "data" in callback:
        action = callback["data"]
        message_id = callback["message"]["message_id"]
        CONTROLLER_MESSAGE_ID = message_id

        if action == "start" and not CHECKER_RUNNING:
            asyncio.create_task(run_checker_loop())
            await edit_message(message_id, "Checker is running...", [[{"text": "Stop", "callback_data": "stop"}]])
        elif action == "stop":
            CHECKER_RUNNING = False
            await edit_message(message_id, "Checker stopped.", [[{"text": "Start", "callback_data": "start"}]])
        elif action == "refresh":
            await edit_message(message_id, "Refreshing proxies...")
            await refresh_proxies()
            await edit_message(message_id, f"Loaded {len(PROXIES)} working proxies.", [[{"text": "Start", "callback_data": "start"}]])

    return {"ok": True}

if __name__ == "__main__":
    load_cached_proxies()
    asyncio.run(set_telegram_webhook())
    asyncio.run(refresh_proxies())

    if not PROXIES:
        print("No valid proxies found. Use /start and then Refresh Proxies in Telegram.")
    else:
        print(f"Loaded {len(PROXIES)} proxies.")

    uvicorn.run(app, host="0.0.0.0", port=8080)
