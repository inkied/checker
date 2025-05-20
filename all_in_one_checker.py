import asyncio
import aiohttp
import os
import random
import string
import json
from aiohttp import web

# === CONFIGURATION ===
TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY", "YOUR_WEBSHARE_API_KEY")

MAX_CONCURRENT_CHECKS = 40
USERNAME_WORDLIST_FILE = "usernames.txt"
BOT_API_URL = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}"

# === GLOBAL STATE ===
PROXY_POOL = []
CHECKER_RUNNING = False
controller_message_id = None

# === USERNAME GENERATION ===
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
            c = random.choice(string.ascii_lowercase)
            name = c * 2 + random.choice(string.ascii_lowercase) * 2
        elif pattern_type == "vowel_blend":
            name = (
                random.choice(string.ascii_lowercase)
                + random.choice(vowels)
                + random.choice(string.ascii_lowercase)
                + random.choice(vowels)
            )
        else:
            name = ''.join(random.choices(string.ascii_lowercase, k=4))
        usernames.add(name)
    return list(usernames)

# === TELEGRAM INTERFACE ===
async def send_telegram_message(session, text, reply_markup=None):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    await session.post(f"{BOT_API_URL}/sendMessage", data=payload)

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
    await session.post(f"{BOT_API_URL}/editMessageText", data=payload)

async def answer_callback_query(session, callback_query_id):
    await session.post(f"{BOT_API_URL}/answerCallbackQuery", data={"callback_query_id": callback_query_id})

def get_controller_keyboard():
    # Buttons stacked vertically (each in its own row)
    return {"inline_keyboard": [
        [{"text": "Start", "callback_data": "start"}],
        [{"text": "Stop", "callback_data": "stop"}],
        [{"text": "Refresh Proxies", "callback_data": "refresh_proxies"}]
    ]}

def get_claim_keyboard(username):
    # Single "Claim" button in its own row
    return {"inline_keyboard": [[{"text": "Claim", "callback_data": f"claim_{username}"}]]}

# === PROXY HANDLING ===
async def fetch_webshare_proxies():
    print("[DEBUG] Scraping Webshare proxies...")
    proxies, page = [], 1
    async with aiohttp.ClientSession() as session:
        while True:
            url = f"https://proxy.webshare.io/api/proxy/list/?page={page}"
            headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    page_proxies = [p.get("proxy_address") for p in data.get("results", []) if p.get("proxy_address")]
                    proxies.extend(page_proxies)
                    if not data.get("next"):
                        break
                    page += 1
            except Exception as e:
                print(f"[DEBUG] Webshare error: {e}")
                break
    return proxies

async def validate_proxy(session, proxy):
    try:
        async with session.get("https://www.tiktok.com", proxy=f"http://{proxy}", timeout=5) as resp:
            return resp.status == 200
    except:
        return False

async def refresh_proxy_pool():
    global PROXY_POOL
    proxies = await fetch_webshare_proxies()
    print(f"[DEBUG] Validating {len(proxies)} proxies...")
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(validate_proxy(session, p) for p in proxies))
    PROXY_POOL = [p for p, ok in zip(proxies, results) if ok]
    print(f"[DEBUG] Valid proxies: {len(PROXY_POOL)}")

# === USERNAME CHECKING ===
async def check_username(session, username, proxy=None):
    try:
        url = f"https://www.tiktok.com/@{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        }
        kwargs = {"headers": headers, "timeout": 10}
        if proxy:
            kwargs["proxy"] = f"http://{proxy}"
        async with session.get(url, **kwargs) as resp:
            return resp.status == 404
    except:
        return False

# === MAIN CHECKER LOOP ===
async def main_checker_loop():
    global CHECKER_RUNNING, controller_message_id
    CHECKER_RUNNING = True
    usernames = []

    if os.path.isfile(USERNAME_WORDLIST_FILE):
        with open(USERNAME_WORDLIST_FILE) as f:
            usernames = [line.strip() for line in f if line.strip()]
    if not usernames:
        usernames = generate_clean_4ls_batch(500)

    await refresh_proxy_pool()
    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

    async with aiohttp.ClientSession() as session:
        if controller_message_id is None:
            resp = await session.post(f"{BOT_API_URL}/sendMessage", data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": "Checker is online",
                "reply_markup": json.dumps(get_controller_keyboard())
            })
            data = await resp.json()
            controller_message_id = data["result"]["message_id"]
        else:
            await edit_telegram_message(session, controller_message_id, "Checker is online", get_controller_keyboard())

        async def worker(username):
            async with sem:
                proxy = random.choice(PROXY_POOL) if PROXY_POOL else None
                if await check_username(session, username, proxy):
                    print(f"[AVAILABLE] {username}")
                    await send_telegram_message(session, f"[{username}](https://www.tiktok.com/@{username})", get_claim_keyboard(username))

        await asyncio.gather(*(worker(u) for u in usernames))

    CHECKER_RUNNING = False
    async with aiohttp.ClientSession() as session:
        await edit_telegram_message(session, controller_message_id, "Checker is offline")

# === TELEGRAM WEBHOOK ===
async def handle_telegram_update(request):
    global CHECKER_RUNNING
    data = await request.json()
    if "callback_query" in data:
        callback = data["callback_query"]
        action = callback["data"]
        callback_id = callback["id"]
        async with aiohttp.ClientSession() as session:
            await answer_callback_query(session, callback_id)
            if action == "start" and not CHECKER_RUNNING:
                asyncio.create_task(main_checker_loop())
            elif action == "stop":
                CHECKER_RUNNING = False
            elif action == "refresh_proxies":
                await refresh_proxy_pool()
            elif action.startswith("claim_"):
                username = action.split("claim_", 1)[1]
                await send_telegram_message(session, f"🚨 You claimed username: {username}")
    return web.Response(text="ok")

# === FASTAPI SERVER ===
def start_server():
    app = web.Application()
    app.router.add_post("/webhook", handle_telegram_update)
    web.run_app(app, port=8000)

if __name__ == "__main__":
    start_server()
