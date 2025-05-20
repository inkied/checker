import asyncio
import aiohttp
import random
import os
import json
import string
from fastapi import FastAPI, Request
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
app = FastAPI()

TELEGRAM_TOKEN = "7527264620:AAGG5qpYqV3o0h0NidwmsTOKxqVsmRIaX1A"
TELEGRAM_CHAT_ID = "7755395640"
WEBSHARE_API_KEY = "cmaqd2pxyf6h1bl93ozf7z12mm2efjsvbd7w366z"
PROXIES_FILE = "proxies.txt"

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing or invalid.")

if not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID is missing or invalid.")

if not WEBSHARE_API_KEY:
    raise ValueError("WEBSHARE_API_KEY is missing.")

BOT_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CHECKER_RUNNING = False
PROXIES = []
controller_message_id = None
CHECKER_SEMAPHORE = asyncio.Semaphore(10)  # concurrency limit


def generate_semi_og_username():
    # Semi-OG style 4-letter usernames with consonant-vowel-consonant-vowel or similar patterns
    consonants = "bcdfghjklmnpqrstvwxyz"
    vowels = "aeiou"
    patterns = [
        lambda: random.choice(consonants) + random.choice(vowels) + random.choice(consonants) + random.choice(vowels),
        lambda: random.choice(vowels) + random.choice(consonants) + random.choice(vowels) + random.choice(consonants),
        lambda: random.choice(consonants) * 2 + random.choice(vowels) * 2,
        lambda: random.choice(vowels) * 2 + random.choice(consonants) * 2,
        lambda: random.choice(consonants) + random.choice(consonants) + random.choice(vowels) + random.choice(consonants)
    ]
    return random.choice(patterns)()


def load_usernames():
    try:
        with open("usernames.txt", "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.warning("usernames.txt not found, will use generator.")
        return []


def load_cached_proxies():
    global PROXIES
    if os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        PROXIES = lines[:100]
        logging.info(f"Loaded {len(PROXIES)} cached proxies (max 100).")
    else:
        PROXIES = []
        logging.info("No cached proxies file found.")


async def validate_proxy(proxy):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.tiktok.com", proxy=proxy, timeout=8) as r:
                return r.status == 200
    except Exception:
        return False


async def send_message(text, buttons=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(f"{BOT_API_URL}/sendMessage", json=payload)
        except Exception as e:
            logging.error(f"send_message error: {e}")


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
        try:
            await session.post(f"{BOT_API_URL}/editMessageText", json=payload)
        except Exception as e:
            logging.error(f"edit_message error: {e}")


async def send_available_username(username):
    buttons = [[{"text": "Claim", "url": f"https://www.tiktok.com/@{username}"}]]
    await send_message(f"<b>@{username}</b> is available.", buttons)


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
            if resp.status == 404:
                return True  # Available
            elif resp.status == 200:
                return False  # Taken
            else:
                return None  # Unknown, maybe proxy error
    except Exception:
        return None


async def refresh_proxies():
    global PROXIES
    logging.info("Fetching proxies from Webshare...")
    await send_message("Starting proxy refresh and validation...")

    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=100&page=1"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                data = await resp.json()
                proxies_raw = data.get("results", [])

        proxies_raw = proxies_raw[:100]

        await send_message(f"Validating {len(proxies_raw)} proxies...")

        raw_proxies = [
            f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in proxies_raw
        ]
        logging.info(f"Pulled {len(raw_proxies)} raw proxies")

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

        await send_message(f"Validated and saved {len(PROXIES)} proxies.")
        logging.info(f"Validated and saved {len(PROXIES)} proxies.")
        if len(PROXIES) < 50:
            logging.warning("Warning: Less than 50 valid proxies found.")

    except Exception as e:
        logging.error(f"Proxy fetch/validation error: {e}")
        await send_message(f"Proxy fetch/validation error: {e}")


async def run_checker_loop():
    global CHECKER_RUNNING
    CHECKER_RUNNING = True
    usernames = load_usernames()
    proxy_pool = PROXIES.copy()
    proxy_index = 0

    async with aiohttp.ClientSession() as session:
        while CHECKER_RUNNING:
            if not usernames:
                username = generate_semi_og_username()
            else:
                username = usernames.pop(0)

            if not proxy_pool:
                await send_message("No working proxies left. Refreshing...")
                await refresh_proxies()
                proxy_pool = PROXIES.copy()
                if not proxy_pool:
                    await send_message("No valid proxies available. Stopping checker.")
                    CHECKER_RUNNING = False
                    break

            proxy = proxy_pool[proxy_index % len(proxy_pool)]

            async with CHECKER_SEMAPHORE:
                result = await check_username(session, username, proxy)

            if result is True:
                await send_available_username(username)
            elif result is None:
                # Remove bad proxy to reduce retries
                if proxy in proxy_pool:
                    proxy_pool.remove(proxy)
                    logging.info(f"Removed invalid proxy: {proxy}")

            proxy_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.2))


@app.post("/webhook")
async def telegram_webhook(request: Request):
    global CHECKER_RUNNING, controller_message_id

    data = await request.json()

    # Handle normal messages
    message = data.get("message", {})
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

    # Handle callback queries (inline buttons)
    callback = data.get("callback_query", {})
    if "data" in callback:
        action = callback["data"]
        message_id = callback["message"]["message_id"]
        controller_message_id = message_id

        if action == "start" and not CHECKER_RUNNING:
            asyncio.create_task(run_checker_loop())
            await edit_message(message_id, "Checker is running...", [
                [{"text": "Stop", "callback_data": "stop"}]
            ])
        elif action == "stop":
            CHECKER_RUNNING = False
            await edit_message(message_id, "Checker stopped.", [
                [{"text": "Start", "callback_data": "start"}]
            ])
        elif action == "refresh":
            await edit_message(message_id, "Refreshing proxies...")
            await refresh_proxies()
            await edit_message(message_id, f"Loaded {len(PROXIES)} working proxies.", [
                [{"text": "Start", "callback_data": "start"}]
            ])
    return {"ok": True}


if __name__ == "__main__":
    load_cached_proxies()
    asyncio.run(refresh_proxies())
    if not PROXIES:
        logging.warning("No valid proxies available after refresh. Use 'Refresh Proxies' in Telegram.")
    else:
        logging.info(f"Loaded {len(PROXIES)} valid proxies after refresh.")

    uvicorn.run(app, host="0.0.0.0", port=8080)
