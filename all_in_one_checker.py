import asyncio
import aiohttp
import aiofiles  # for async file I/O
import random
import os
import json
import string
from fastapi import FastAPI, Request
from aiohttp import ClientSession
from typing import List
import uvicorn

app = FastAPI()

TELEGRAM_TOKEN = "7527264620:AAGG5qpYqV3o0h0NidwmsTOKxqVsmRIaX1A"
TELEGRAM_CHAT_ID = "7755395640"  # Set directly, no os.getenv fallback needed here
WEBSHARE_API_KEY = "cmaqd2pxyf6h1bl93ozf7z12mm2efjsvbd7w366z"

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing or invalid.")

if not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID is missing or invalid.")

if not WEBSHARE_API_KEY:
    raise ValueError("WEBSHARE_API_KEY is missing. Please set it as an environment variable.")

BOT_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CHECKER_RUNNING = False
PROXIES: List[str] = []
controller_message_id = None

VALID_PROXIES_FILE = "valid_proxies.txt"
PROXY_BATCH_SIZE = 50
PROXY_FETCH_SIZE = 100

# === Utility Functions ===

def generate_username():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))


def load_usernames():
    try:
        with open("usernames.txt", "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


# === Improved Proxy Fetch, Validate, Cache Functions ===

async def get_proxies_from_webshare(page=1):
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    url = f"https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size={PROXY_FETCH_SIZE}&page={page}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return []
            data = await response.json()
            return [
                f"http://{p['username']}:{p['password']}@{p['ip']}:{p['port']}"
                for p in data.get("results", [])
            ]


async def validate_proxy(proxy):
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://www.tiktok.com", proxy=proxy) as resp:
                return resp.status == 200
    except Exception:
        return False


async def load_cached_proxies():
    try:
        async with aiofiles.open(VALID_PROXIES_FILE, "r") as f:
            lines = await f.readlines()
            return [line.strip() for line in lines if line.strip()]
    except FileNotFoundError:
        return []


async def save_valid_proxies(proxies):
    async with aiofiles.open(VALID_PROXIES_FILE, "w") as f:
        await f.write("\n".join(proxies))


async def refresh_proxies():
    global PROXIES
    PROXIES = await load_cached_proxies()
    if PROXIES:
        print(f"Loaded {len(PROXIES)} cached proxies.")
        return
    
    print("Fetching proxies from Webshare...")
    raw_proxies = await get_proxies_from_webshare()
    if not raw_proxies:
        print("Failed to fetch proxies.")
        PROXIES = []
        return

    print(f"Validating {len(raw_proxies)} proxies in batches of {PROXY_BATCH_SIZE}...")
    valid_proxies = []

    for i in range(0, len(raw_proxies), PROXY_BATCH_SIZE):
        batch = raw_proxies[i:i+PROXY_BATCH_SIZE]
        results = await asyncio.gather(*(validate_proxy(p) for p in batch))
        for proxy, is_valid in zip(batch, results):
            if is_valid:
                valid_proxies.append(proxy)
        await asyncio.sleep(1)  # pause between batches

    PROXIES = valid_proxies
    await save_valid_proxies(valid_proxies)
    print(f"Validated {len(valid_proxies)} working proxies.")


# === Telegram Bot Logic ===

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
    await send_message(f"✅ <b>@{username}</b> is <u>available</u>!", buttons)


# === Checker Logic ===

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
        return None  # Indicate proxy issue


async def run_checker_loop():
    global CHECKER_RUNNING
    CHECKER_RUNNING = True
    usernames = load_usernames()
    used_usernames = set()
    proxy_pool = PROXIES.copy()
    proxy_index = 0

    async with aiohttp.ClientSession() as session:
        while CHECKER_RUNNING:
            if not usernames:
                username = generate_username()
            else:
                username = usernames.pop(0)
                used_usernames.add(username)

            if not proxy_pool:
                await send_message("⚠️ No working proxies left. Refreshing...")
                await refresh_proxies()
                proxy_pool = PROXIES.copy()
                if not proxy_pool:
                    await send_message("❌ No valid proxies available. Stopping checker.")
                    CHECKER_RUNNING = False
                    break

            proxy = proxy_pool[proxy_index % len(proxy_pool)]
            result = await check_username(session, username, proxy)

            if result is True:
                await send_available_username(username)
            elif result is None:
                proxy_pool.remove(proxy)
            proxy_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.2))


# === FastAPI Telegram Webhook ===

@app.post("/webhook")
async def telegram_webhook(request: Request):
    global CHECKER_RUNNING, controller_message_id

    data = await request.json()
    message = data.get("message", {})
    callback = data.get("callback_query", {})

    if "text" in message:
        text = message["text"]
        if text == "/start":
            buttons = [[
                {"text": "▶️ Start", "callback_data": "start"},
                {"text": "⛔ Stop", "callback_data": "stop"},
                {"text": "🔁 Refresh Proxies", "callback_data": "refresh"}
            ]]
            await send_message("🔧 <b>Checker Controls:</b>", buttons)
        return {"ok": True}

    if "data" in callback:
        action = callback["data"]
        message_id = callback["message"]["message_id"]
        controller_message_id = message_id

        if action == "start" and not CHECKER_RUNNING:
            asyncio.create_task(run_checker_loop())
            await edit_message(message_id, "✅ Checker is running...", [
                [{"text": "⛔ Stop", "callback_data": "stop"}]
            ])

        elif action == "stop":
            CHECKER_RUNNING = False
            await edit_message(message_id, "🛑 Checker stopped.", [
                [{"text": "▶️ Start", "callback_data": "start"}]
            ])

        elif action == "refresh":
            await edit_message(message_id, "♻️ Refreshing proxies...")
            await refresh_proxies()
            await edit_message(message_id, f"✅ Loaded {len(PROXIES)} working proxies.", [
                [{"text": "▶️ Start", "callback_data": "start"}]
            ])

    return {"ok": True}


# === Run with Uvicorn (if running locally) ===
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
