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

MAX_CONCURRENT = 30  # Max concurrent checks to keep safe for TikTok
BATCH_DELAY = 20  # seconds delay between batches

# Proxy files
WEBSHARE_PROXY_FILE = "webshare_proxies.txt"
FREE_PROXY_SCRAPE_INTERVAL = 600  # every 10 minutes

USERNAME_WORDLIST_FILE = "usernames.txt"
AVAILABLE_USERNAMES_FILE = "available_usernames.txt"

# === GLOBALS ===
proxy_pool = set()  # all proxies, string format like user:pass@ip:port or ip:port
valid_proxies = set()  # proxies verified alive and working
username_queue = asyncio.Queue()
stop_event = asyncio.Event()
telegram_semaphore = asyncio.Semaphore(1)
check_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
last_batch_time = 0

# Telegram bot base URL
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Regex for proxy format detection
PROXY_AUTH_REGEX = re.compile(r"^(?:(?P<user>[^:@]+):(?P<pass>[^@]+)@)?(?P<ip>[^:]+):(?P<port>\d+)$")

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
            async with session.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload) as resp:
                return await resp.json()

async def get_webshare_proxies():
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    proxies = set()
    page = 1
    while True:
        params = {"page": page}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(WEBSHARE_API_ENDPOINT, params=params) as resp:
                if resp.status != 200:
                    print(f"[Webshare] Failed to get proxies: {resp.status}")
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
    print(f"[Webshare] Fetched {len(proxies)} proxies")
    return proxies

async def load_proxies_from_file(filename):
    proxies = set()
    try:
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    proxies.add(line)
    except FileNotFoundError:
        pass
    return proxies

async def scrape_free_proxies():
    # Simplified proxy scraping from public sources example
    # For demo, returns empty set - add your scraping logic here
    return set()

async def validate_proxy(proxy):
    try:
        m = PROXY_AUTH_REGEX.match(proxy)
        if not m:
            return False
        user, pwd, ip, port = m.group("user"), m.group("pass"), m.group("ip"), m.group("port")

        if user and pwd:
            connector = ProxyConnector(proxy_type=ProxyType.HTTP, host=ip, port=int(port), username=user, password=pwd)
        else:
            connector = ProxyConnector(proxy_type=ProxyType.HTTP, host=ip, port=int(port))

        timeout = aiohttp.ClientTimeout(total=7)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get("http://httpbin.org/ip") as resp:
                if resp.status == 200:
                    return True
    except:
        pass
    return False

async def refresh_proxy_pool():
    global proxy_pool, valid_proxies
    while not stop_event.is_set():
        print("[Proxy] Refreshing proxy pool...")
        # Load Webshare proxies via API
        webshare_proxies = await get_webshare_proxies()

        # Load proxies from file as backup
        file_proxies = await load_proxies_from_file(WEBSHARE_PROXY_FILE)

        # Scrape free proxies every X minutes
        free_proxies = await scrape_free_proxies()

        combined = set()
        combined.update(webshare_proxies)
        combined.update(file_proxies)
        combined.update(free_proxies)

        print(f"[Proxy] Total proxies fetched: {len(combined)}")

        # Validate proxies concurrently but limited to safe number
        sem = asyncio.Semaphore(20)
        valid_proxies.clear()

        async def validate_and_add(p):
            async with sem:
                if await validate_proxy(p):
                    valid_proxies.add(p)

        validation_tasks = [validate_and_add(p) for p in combined]
        await asyncio.gather(*validation_tasks)

        proxy_pool = valid_proxies.copy()
        print(f"[Proxy] Valid proxies: {len(proxy_pool)}")
        await asyncio.sleep(FREE_PROXY_SCRAPE_INTERVAL)

async def check_username_availability(username, proxy):
    try:
        m = PROXY_AUTH_REGEX.match(proxy)
        if not m:
            return False
        user, pwd, ip, port = m.group("user"), m.group("pass"), m.group("ip"), m.group("port")
        if user and pwd:
            connector = ProxyConnector(proxy_type=ProxyType.HTTP, host=ip, port=int(port), username=user, password=pwd)
        else:
            connector = ProxyConnector(proxy_type=ProxyType.HTTP, host=ip, port=int(port))

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            url = f"https://www.tiktok.com/@{username}"

            async with session.get(url) as resp:
                if resp.status == 404:
                    return True
                else:
                    return False
    except:
        return False

async def checker_worker():
    while not stop_event.is_set():
        username = await username_queue.get()
        async with check_semaphore:
            proxy = random.choice(list(proxy_pool)) if proxy_pool else None
            if not proxy:
                username_queue.put_nowait(username)
                await asyncio.sleep(5)
                continue

            available = await check_username_availability(username, proxy)
            if available:
                with open(AVAILABLE_USERNAMES_FILE, "a") as f:
                    f.write(username + "\n")
                await send_telegram_message(f"✅ Available username: <b>{username}</b>\nhttps://www.tiktok.com/@{username}")

        username_queue.task_done()
        await asyncio.sleep(0.1)

async def load_usernames():
    try:
        with open(USERNAME_WORDLIST_FILE, "r") as f:
            for line in f:
                username = line.strip()
                if username:
                    await username_queue.put(username)
    except FileNotFoundError:
        print(f"Wordlist file '{USERNAME_WORDLIST_FILE}' not found.")

async def telegram_bot_handler():
    offset = None
    while True:
        params = {"timeout": 100}
        if offset is not None:
            params["offset"] = offset
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
                        if not stop_event.is_set():
                            stop_event.clear()
                            asyncio.create_task(run_checker())
                            await send_telegram_message("✅ Checker started.")
                        else:
                            await send_telegram_message("✅ Checker already running.")
                    elif text == "/stop":
                        stop_event.set()
                        await send_telegram_message("🛑 Checker stopped.")
        await asyncio.sleep(1)

async def run_checker():
    await load_usernames()
    proxy_task = asyncio.create_task(refresh_proxy_pool())
    workers = [asyncio.create_task(checker_worker()) for _ in
