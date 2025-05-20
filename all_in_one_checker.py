import asyncio
import aiohttp
import random
import string
import os

# === CONFIGURATION ===

TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY", "YOUR_WEBSHARE_API_KEY")

MAX_CONCURRENT_CHECKS = 40
USERNAME_WORDLIST_FILE = "usernames.txt"

# Global state
PROXY_POOL = []
CHECKER_RUNNING = False

# === LIVE USERNAME GENERATION ===

def generate_clean_4ls_batch(batch_size=100):
    prefixes = ["ts", "lx", "zn", "cr", "vx", "pl", "kl", "tr", "bl", "dr"]
    vowels = "aeiou"
    suffixes = ["la", "xo", "ra", "on", "ix", "um", "in", "is", "or", "ek"]
    
    usernames = set()
    while len(usernames) < batch_size:
        pattern_type = random.choice(["prefix_suffix", "repeater", "vowel_blend", "semi_og"])

        if pattern_type == "prefix_suffix":
            name = random.choice(prefixes) + random.choice(suffixes)
        elif pattern_type == "repeater":
            char = random.choice(string.ascii_lowercase)
            name = char * 2 + random.choice(string.ascii_lowercase) * 2
        elif pattern_type == "vowel_blend":
            name = (
                random.choice(string.ascii_lowercase)
                + random.choice(vowels)
                + random.choice(string.ascii_lowercase)
                + random.choice(vowels)
            )
        else:  # semi_og
            name = "".join(random.choices(string.ascii_lowercase, k=4))

        usernames.add(name)

    return list(usernames)

# === PROXY SCRAPING & VALIDATION ===

async def fetch_webshare_proxies():
    print("[DEBUG] Scraping Webshare proxies...")
    proxies = []
    page = 1
    async with aiohttp.ClientSession() as session:
        while True:
            url = f"https://proxy.webshare.io/api/proxy/list/?page={page}"
            headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        print(f"[DEBUG] Webshare API returned status {resp.status}, stopping scrape.")
                        break
                    data = await resp.json()
                    page_proxies = [p.get("proxy_address") for p in data.get("results", []) if p.get("proxy_address")]
                    proxies.extend(page_proxies)
                    if not data.get("next"):
                        break
                    page += 1
            except Exception as e:
                print(f"[DEBUG] Exception scraping proxies: {e}")
                break
    print(f"[DEBUG] Total scraped proxies: {len(proxies)}")
    return proxies

async def validate_proxy(session, proxy):
    try:
        test_url = "https://www.tiktok.com"
        proxy_url = f"http://{proxy}"
        async with session.get(test_url, proxy=proxy_url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

async def refresh_proxy_pool():
    global PROXY_POOL
    proxies = await fetch_webshare_proxies()
    print(f"[DEBUG] Validating {len(proxies)} proxies...")
    valid_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [validate_proxy(session, proxy) for proxy in proxies]
        results = await asyncio.gather(*tasks)
    for proxy, valid in zip(proxies, results):
        if valid:
            valid_proxies.append(proxy)
        else:
            print(f"[DEBUG] Invalid proxy discarded: {proxy}")
    PROXY_POOL = valid_proxies
    print(f"[DEBUG] Proxy pool refreshed: {len(PROXY_POOL)} valid proxies available.")

# === TELEGRAM INTERFACE ===

from aiohttp import web
import json

BOT_API_URL = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}"

async def send_telegram_message(session, text, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with session.post(f"{BOT_API_URL}/sendMessage", data=payload) as resp:
        if resp.status != 200:
            print(f"[DEBUG] Failed to send Telegram message: {await resp.text()}")

async def edit_telegram_message(session, message_id, text, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with session.post(f"{BOT_API_URL}/editMessageText", data=payload) as resp:
        if resp.status != 200:
            print(f"[DEBUG] Failed to edit Telegram message: {await resp.text()}")

async def answer_callback_query(session, callback_query_id):
    payload = {"callback_query_id": callback_query_id}
    async with session.post(f"{BOT_API_URL}/answerCallbackQuery", data=payload) as resp:
        if resp.status != 200:
            print(f"[DEBUG] Failed to answer callback query: {await resp.text()}")

# === TIKTOK USERNAME CHECKER ===

async def check_username(session, username, proxy=None):
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    try:
        kwargs = {"headers": headers, "timeout": 10}
        if proxy:
            kwargs["proxy"] = f"http://{proxy}"
        async with session.get(url, **kwargs) as resp:
            # 404 means available username
            return resp.status == 404
    except Exception:
        return False

# === GLOBAL CHECKER CONTROL ===

# Store message_id of the controller message with buttons to update inline keyboard
controller_message_id = None

def get_controller_keyboard():
    buttons = [
        [{"text": "Start", "callback_data": "start"}],
        [{"text": "Stop", "callback_data": "stop"}],
        [{"text": "Refresh Proxies", "callback_data": "refresh_proxies"}],
    ]
    return {"inline_keyboard": buttons}

def get_claim_keyboard(username):
    # Only claim button (no skip)
    return {"inline_keyboard": [[{"text": "Claim", "callback_data": f"claim_{username}"}]]}

# === MAIN LOOP AND TELEGRAM WEBHOOK HANDLER ===

async def main_checker_loop():
    global CHECKER_RUNNING, PROXY_POOL
    CHECKER_RUNNING = True

    # Send or update control message with buttons
    async with aiohttp.ClientSession() as session:
        text = "Checker is online"
        # If first time, send message and save message_id
        global controller_message_id
        if controller_message_id is None:
            msg = await session.post(f"{BOT_API_URL}/sendMessage", data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "reply_markup": json.dumps(get_controller_keyboard())
            })
            resp_json = await msg.json()
            if resp_json.get("ok"):
                controller_message_id = resp_json["result"]["message_id"]
        else:
            await edit_telegram_message(session, controller_message_id, text, get_controller_keyboard())

        # Load usernames fallback or generate live
        if os.path.isfile(USERNAME_WORDLIST_FILE):
            with open(USERNAME_WORDLIST_FILE, "r") as f:
                usernames = [line.strip() for line in f if line.strip()]
            print(f"[DEBUG] Loaded {len(usernames)} usernames from wordlist.")
        else:
            usernames = generate_clean_4ls_batch(500)
            print(f"[DEBUG] Generated {len(usernames)} live usernames.")

        # Refresh proxies before starting checks
        await refresh_proxy_pool()

        sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

        async with aiohttp.ClientSession() as session:
            async def worker(username):
                async with sem:
                    proxy = random.choice(PROXY_POOL) if PROXY_POOL else None
                    is_available = await check_username(session, username, proxy)
                    if is_available and CHECKER_RUNNING:
                        print(f"[AVAILABLE] {username}")
                        # Send Telegram message per user with claim button
                        await send_telegram_message(
                            session,
                            f"[{username}](https://www.tiktok.com/@{username})",
                            reply_markup=get_claim_keyboard(username)
                        )

            tasks = [worker(username) for username in usernames]
            await asyncio.gather(*tasks)

    CHECKER_RUNNING = False
    # After done, update controller message to offline and remove buttons
    async with aiohttp.ClientSession() as session:
        if controller_message_id:
            await edit_telegram_message(session, controller_message_id, "Checker is offline", reply_markup=None)

async def handle_telegram_update(request):
    global CHECKER_RUNNING
    data = await request.json()

    # Handle message or callback query
    if "callback_query" in data:
        callback = data["callback_query"]
        callback_id = callback["id"]
        data_payload = callback["data"]
        from_user = callback["from"]["id"]

        async with aiohttp.ClientSession() as session:
            await answer_callback_query(session, callback_id)

            if data_payload
