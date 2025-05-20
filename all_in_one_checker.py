import asyncio
import aiohttp
import time
import random
import json
from aiohttp_socks import ProxyConnector, ProxyType
import re

# === CONFIG ===
TELEGRAM_BOT_TOKEN = "7527264620:AAGG5qpYqV3o0h0NidwmsTOKxqVsmRIaX1A"
TELEGRAM_CHAT_ID = "7755395640"

# Webshare API details
WEBSHARE_API_KEY = "cmaqd2pxyf6h1bl93ozf7z12mm2efjsvbd7w366z"
WEBSHARE_PROXY_USERNAME = "trdwseke-rotate"
WEBSHARE_PROXY_PASSWORD = "n0vc7b0ev31y"
WEBSHARE_API_ENDPOINT = "https://proxy.webshare.io/api/proxy/list/"

MAX_CONCURRENT = 30
AVAILABLE_USERNAMES_FILE = "available_usernames.txt"

proxy_pool = set()
valid_proxies = set()
username_queue = asyncio.Queue()
stop_event = asyncio.Event()
telegram_semaphore = asyncio.Semaphore(1)
check_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
PROXY_AUTH_REGEX = re.compile(r"^(?:(?P<user>[^:@]+):(?P<pass>[^@]+)@)?(?P<ip>[^:]+):(?P<port>\d+)$")

# === Username generation ===

# Hardcoded pronounceable / brand-like 4-letter usernames (expand as needed)
PRONOUNCEABLE_4L = [
    "tsla", "movo", "beko", "lomo", "zino", "vexo", "kiri", "rivo",
    "nolo", "sira", "kato", "bano", "lira", "tavo", "fino", "rako"
]

VOWELS = "aeiou"
CONSONANTS = "bcdfghjklmnpqrstvwxyz"

def generate_random_4l():
    # Simple random pronounceable pattern: consonant + vowel + consonant + vowel
    return "".join([
        random.choice(CONSONANTS),
        random.choice(VOWELS),
        random.choice(CONSONANTS),
        random.choice(VOWELS),
    ])

async def generate_username():
    # Try to pop from pronounceable list first, else fallback to random
    if PRONOUNCEABLE_4L:
        username = PRONOUNCEABLE_4L.pop(random.randint(0, len(PRONOUNCEABLE_4L) - 1))
        return username
    else:
        return generate_random_4l()

# === FUNCTIONS ===

async def send_telegram_message(text):
    async with telegram_semaphore:
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            await session.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)

async def get_webshare_proxies():
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    proxies = set()
    page = 1
    while True:
        params = {"page": page}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(WEBSHARE_API_ENDPOINT, params=params) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                results = data.get("results", [])
                if not results:
                    break
                for p in results:
                    ip = p.get("proxy_address")
                    port = p.get("proxy_port")
                    username = WEBSHARE_PROXY_USERNAME
                    password = WEBSHARE_PROXY_PASSWORD
                    proxy_str = f"{username}:{password}@{ip}:{port}"
                    proxies.add(proxy_str)
                if not data.get("next"):
                    break
                page += 1
    return proxies

async def validate_proxy(proxy):
    try:
        m = PROXY_AUTH_REGEX.match(proxy)
        if not m:
            print(f"[DEBUG] Invalid proxy format: {proxy}")
            return False
        user, pwd, ip, port = m.group("user"), m.group("pass"), m.group("ip"), m.group("port")

        connector = ProxyConnector(
            proxy_type=ProxyType.HTTP,
            host=ip,
            port=int(port),
            username=user,
            password=pwd,
        )

        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get("http://httpbin.org/ip") as resp:
                if resp.status == 200:
                    return True
    except Exception as e:
        print(f"[DEBUG] Proxy failed: {proxy} | Error: {e}")
    return False

async def refresh_proxy_pool():
    global proxy_pool, valid_proxies
    while not stop_event.is_set():
        print("[DEBUG] Scraping Webshare proxies...")
        proxies = await get_webshare_proxies()
        print(f"[DEBUG] {len(proxies)} proxies scraped. Validating...")

        validated = set()
        sem = asyncio.Semaphore(30)

        async def validate_and_store(p):
            async with sem:
                if await validate_proxy(p):
                    validated.add(p)

        await asyncio.gather(*[validate_and_store(p) for p in proxies])
        valid_proxies.clear()
        valid_proxies.update(validated)
        proxy_pool.clear()
        proxy_pool.update(validated)

        print(f"[DEBUG] Validation complete. {len(valid_proxies)} valid proxies.")
        await asyncio.sleep(600)

async def check_username_availability(username, proxy):
    try:
        m = PROXY_AUTH_REGEX.match(proxy)
        if not m:
            return False
        user, pwd, ip, port = m.group("user"), m.group("pass"), m.group("ip"), m.group("port")
        connector = ProxyConnector(
            proxy_type=ProxyType.HTTP,
            host=ip,
            port=int(port),
            username=user,
            password=pwd,
        )

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            url = f"https://www.tiktok.com/@{username}"
            async with session.get(url) as resp:
                return resp.status == 404
    except Exception as e:
        print(f"[DEBUG] Username check failed for {username} with proxy {proxy} | Error: {e}")
        return False

async def checker_worker():
    while not stop_event.is_set():
        username = await username_queue.get()
        async with check_semaphore:
            proxy = random.choice(list(proxy_pool)) if proxy_pool else None
            if not proxy:
                # No proxies right now, requeue username and wait a bit
                await username_queue.put(username)
                await asyncio.sleep(5)
                username_queue.task_done()
                continue

            available = await check_username_availability(username, proxy)
            if available:
                with open(AVAILABLE_USERNAMES_FILE, "a") as f:
                    f.write(username + "\n")
                await send_telegram_message(f"✅ Available: <b>{username}</b>\nhttps://www.tiktok.com/@{username}")

        username_queue.task_done()
        await asyncio.sleep(0.1)

async def username_feeder():
    while not stop_event.is_set():
        # Keep generating and enqueueing usernames as long as stopped is False
        username = await generate_username()
        await username_queue.put(username)
        await asyncio.sleep(0.05)  # small delay to avoid queue spam

async def telegram_bot_handler():
    offset = None
    while True:
        params = {"timeout": 100, "offset": offset}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TELEGRAM_API_URL}/getUpdates", params=params) as resp:
                data = await resp.json()
                for result in data.get("result", []):
                    offset = result["update_id"] + 1
                    message = result.get("message")
                    if not message:
                        continue
                    text = message.get("text", "").lower()
                    chat_id = message["chat"]["id"]

                    if chat_id != int(TELEGRAM_CHAT_ID):
                        continue

                    if text == "/start":
                        if stop_event.is_set():
                            stop_event.clear()
                            asyncio.create_task(run_checker())
                            await send_telegram_message("✅ Checker started.")
                        else:
                            await send_telegram_message("✅ Already running.")
                    elif text == "/stop":
                        stop_event.set()
                        await send_telegram_message("🛑 Checker stopped.")
        await asyncio.sleep(1)

async def run_checker():
    # Start the proxy refresher, username feeder, and checker workers
    proxy_task = asyncio.create_task(refresh_proxy_pool())
    feeder_task = asyncio.create_task(username_feeder())
    workers = [asyncio.create_task(checker_worker()) for _ in range(MAX_CONCURRENT)]

    await username_queue.join()  # Wait until the queue is empty (which is unlikely here, runs forever)
    stop_event.set()
    proxy_task.cancel()
    feeder_task.cancel()
    for w in workers:
        w.cancel()

async def main():
    bot_task = asyncio.create_task(telegram_bot_handler())
    await bot_task

if __name__ == "__main__":
    try:
        asyncio.run(main())
