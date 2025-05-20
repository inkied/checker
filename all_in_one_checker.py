import asyncio
import aiohttp
import random
import os
import json
import string
import signal
import logging
from fastapi import FastAPI, Request
from aiohttp import ClientSession
from typing import List
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7527264620:AAGG5qpYqV3o0h0NidwmsTOKxqVsmRIaX1A")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7755395640")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY", "cmaqd2pxyf6h1bl93ozf7z12mm2efjsvbd7w366z")

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

# Graceful shutdown support
stop_event = asyncio.Event()

def generate_username():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def load_usernames():
    try:
        with open("usernames.txt", "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

async def get_proxies_from_webshare():
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=100&page=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            data = await response.json()
            return [
                f"http://{p['username']}:{p['password']}@{p['ip']}:{p['port']}"
                for p in data.get("results", [])
            ]

async def validate_proxy(proxy):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.tiktok.com", proxy=proxy, timeout=8) as r:
                return r.status == 200
    except Exception as e:
        logger.debug(f"Proxy validation failed for {proxy}: {e}")
        return False

async def refresh_proxies():
    global PROXIES
    logger.info("Refreshing proxies from Webshare...")
    raw_proxies = await get_proxies_from_webshare()
    valid_proxies = []

    async def validate_and_collect(proxy):
        full_proxy = proxy if proxy.startswith("http") else f"http://{proxy}"
        if await validate_proxy(full_proxy):
            valid_proxies.append(full_proxy)

    await asyncio.gather(*[validate_and_collect(p) for p in raw_proxies])
    PROXIES = valid_proxies.copy()
    logger.info(f"Loaded {len(PROXIES)} valid proxies.")

async def send_message(text, buttons=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    async with aiohttp.ClientSession() as session:
        resp = await session.post(f"{BOT_API_URL}/sendMessage", json=payload)
        if resp.status != 200:
            text_resp = await resp.text()
            logger.warning(f"Telegram sendMessage failed: {resp.status} {text_resp}")
        return await resp.json()

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
        resp = await session.post(f"{BOT_API_URL}/editMessageText", json=payload)
        if resp.status != 200:
            text_resp = await resp.text()
            logger.warning(f"Telegram editMessageText failed: {resp.status} {text_resp}")
        return await resp.json()

async def send_available_username(username):
    buttons = [[{"text": "Claim", "url": f"https://www.tiktok.com/@{username}"}]]
    await send_message(f"✅ <b>@{username}</b> is <u>available</u>!", buttons)

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
    except Exception as e:
        logger.debug(f"Check failed for {username} with proxy {proxy}: {e}")
        return None

async def run_checker_loop():
    global CHECKER_RUNNING
    CHECKER_RUNNING = True
    usernames = load_usernames()
    proxy_pool = PROXIES.copy()
    proxy_index = 0

    async with aiohttp.ClientSession() as session:
        while CHECKER_RUNNING and not stop_event.is_set():
            if not usernames:
                username = generate_username()
            else:
                username = usernames.pop(0)

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
                logger.debug(f"Removed bad proxy: {proxy}")

            proxy_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.2))

@app.post("/webhook")
async def telegram_webhook(request: Request):
    global CHECKER_RUNNING, controller_message_id
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return {"ok": False}

    # Distinguish update types
    if "callback_query" in data:
        callback = data["callback_query"]
        action = callback.get("data")
        message_id = callback["message"]["message_id"]
        controller_message_id = message_id

        if action == "start" and not CHECKER_RUNNING:
            asyncio.create_task(run_checker_loop())
            await edit_message(message_id, "✅ Checker is running...", [[{"text": "⛔ Stop", "callback_data": "stop"}]])
        elif action == "stop":
            CHECKER_RUNNING = False
            await edit_message(message_id, "🛑 Checker stopped.", [[{"text": "▶️ Start", "callback_data": "start"}]])
        elif action == "refresh":
            await edit_message(message_id, "♻️ Refreshing proxies...")
            await refresh_proxies()
            await edit_message(message_id, f"✅ Loaded {len(PROXIES)} working proxies.", [[{"text": "▶️ Start", "callback_data": "start"}]])

    elif "message" in data:
        message = data["message"]
        text = message.get("text", "")
        if text == "/start":
            buttons = [[
                {"text": "▶️ Start", "callback_data": "start"},
                {"text": "⛔ Stop", "callback_data": "stop"},
                {"text": "🔁 Refresh Proxies", "callback_data": "refresh"}
            ]]
            await send_message("🔧 <b>Checker Controls:</b>", buttons)

    else:
        logger.warning("Unknown update type received")

    return {"ok": True}

def shutdown():
    global CHECKER_RUNNING
    logger.info("Shutdown signal received. Stopping checker...")
    CHECKER_RUNNING = False
    stop_event.set()

if __name__ == "__main__":
    # Graceful shutdown on SIGTERM (Railway friendly)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
