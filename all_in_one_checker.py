import asyncio
import aiohttp
import random
import string
import re
import os

# === CONFIGURATION ===

# Telegram bot config (put your real tokens here or env vars)
TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")

# Webshare API key for proxy scraping
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY", "YOUR_WEBSHARE_API_KEY")

# Proxy pool and concurrency
PROXY_POOL = []
MAX_CONCURRENT_CHECKS = 40

# Username source file fallback (optional)
USERNAME_WORDLIST_FILE = "usernames.txt"

# Telegram batch size for messages
TELEGRAM_BATCH_SIZE = 10

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
        elif pattern_type == "semi_og":
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
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[DEBUG] Webshare API returned status {resp.status}, stopping scrape.")
                    break
                data = await resp.json()
                page_proxies = [p.get("proxy_address") for p in data.get("results", []) if p.get("proxy_address")]
                proxies.extend(page_proxies)
                if not data.get("next"):
                    break
                page += 1
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

# === TELEGRAM ALERTS ===

async def send_telegram_message(session, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    async with session.post(url, data=payload) as resp:
        if resp.status != 200:
            print(f"[DEBUG] Failed to send Telegram message: {await resp.text()}")

async def batch_send_telegram_messages(usernames):
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(usernames), TELEGRAM_BATCH_SIZE):
            batch = usernames[i:i+TELEGRAM_BATCH_SIZE]
            text = "\n".join(
                f"[{u}](https://www.tiktok.com/@{u})"
                for u in batch
            )
            await send_telegram_message(session, text)

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
            # TikTok returns 404 if username not taken (available)
            return resp.status == 404
    except Exception:
        return False

async def main_loop():
    global PROXY_POOL
    # Load usernames from file fallback if exists, else generate live
    if os.path.isfile(USERNAME_WORDLIST_FILE):
        with open(USERNAME_WORDLIST_FILE, "r") as f:
            usernames = [line.strip() for line in f if line.strip()]
        print(f"[DEBUG] Loaded {len(usernames)} usernames from wordlist.")
    else:
        usernames = generate_clean_4ls_batch(500)
        print(f"[DEBUG] Generated {len(usernames)} live usernames.")

    # Refresh proxies before start
    await refresh_proxy_pool()

    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    available_usernames = []

    async with aiohttp.ClientSession() as session:
        async def worker(username):
            async with sem:
                proxy = random.choice(PROXY_POOL) if PROXY_POOL else None
                is_available = await check_username(session, username, proxy)
                if is_available:
                    available_usernames.append(username)
                    print(f"[AVAILABLE] {username}")

        tasks = [worker(username) for username in usernames]
        await asyncio.gather(*tasks)

    if available_usernames:
        print(f"[DEBUG] Found {len(available_usernames)} available usernames. Sending Telegram alert...")
        await batch_send_telegram_messages(available_usernames)
    else:
        print("[DEBUG] No available usernames found.")

if __name__ == "__main__":
    asyncio.run(main_loop())
