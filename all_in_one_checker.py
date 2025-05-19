import os
import requests
import threading
import time
import random
import re
import json
import asyncio
import aiohttp

# Telegram Bot info
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_LIST = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64)...',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64)...'
]

proxy_pool = []
proxy_usage = {}
proxy_lock = threading.Lock()
MAX_THREADS = 20
running = False

def send_telegram_message(message, buttons=False):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }

    if buttons:
        payload['reply_markup'] = json.dumps({
            'inline_keyboard': [[
                {'text': '▶️ Start', 'callback_data': 'start'},
                {'text': '⏹ Stop', 'callback_data': 'stop'}
            ]]
        })

    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"[Telegram error] {e}")

# --- ASYNC PROXY SCRAPER AND VALIDATOR ---

async def fetch_proxies():
    print("[INFO] Scraping proxies...")
    sources = [
        "https://www.proxy-list.download/api/v1/get?type=http",
        "https://www.proxy-list.download/api/v1/get?type=https",
        "https://www.proxy-list.download/api/v1/get?type=socks4",
        "https://www.proxy-list.download/api/v1/get?type=socks5",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    ]

    proxies = set()

    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in sources:
            tasks.append(fetch_proxy_list(session, url, proxies))
        await asyncio.gather(*tasks)

    return list(proxies)

async def fetch_proxy_list(session, url, proxies):
    try:
        async with session.get(url, timeout=10) as resp:
            text = await resp.text()
            found = re.findall(r'(\d{1,3}(?:\.\d{1,3}){3}:\d+)', text)
            for proxy in found:
                proxies.add(f"http://{proxy}")
    except:
        pass

async def validate_proxy(session, proxy, valid_proxies):
    try:
        headers = {'User-Agent': random.choice(HEADERS_LIST)}
        async with session.get("https://www.tiktok.com", proxy=proxy, headers=headers, timeout=2.5) as resp:
            if resp.status == 200:
                valid_proxies.append(proxy)
    except:
        pass

async def scrape_proxies_async():
    all_proxies = await fetch_proxies()
    print(f"[INFO] Validating {len(all_proxies)} proxies...")

    valid_proxies = []
    connector = aiohttp.TCPConnector(limit=30)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for proxy in all_proxies:
            tasks.append(validate_proxy(session, proxy, valid_proxies))
        await asyncio.gather(*tasks)

    with proxy_lock:
        proxy_pool[:] = valid_proxies
        proxy_usage.clear()

def scrape_proxies():
    asyncio.run(scrape_proxies_async())

# --- OTHER FUNCTIONS ---

def periodic_proxy_rescrape():
    while True:
        time.sleep(600)
        scrape_proxies()

def get_proxy():
    with proxy_lock:
        if not proxy_pool:
            return None
        sorted_proxies = sorted(proxy_pool, key=lambda p: proxy_usage.get(p, 0))
        proxy = sorted_proxies[0]
        proxy_usage[proxy] = proxy_usage.get(proxy, 0) + 1
        return proxy

def generate_usernames(count=100):
    usernames = set()
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    while len(usernames) < count:
        length = random.choice([4,5,6])
        name = ''.join(random.choice(chars) for _ in range(length))
        usernames.add(name)
    return list(usernames)

def check_username(username):
    proxy = get_proxy()
    if proxy is None:
        return
    headers = {'User-Agent': random.choice(HEADERS_LIST)}
    url = f"https://www.tiktok.com/@{username}"
    try:
        r = requests.get(url, headers=headers, proxies={"http": proxy, "https": proxy}, timeout=10)
        if r.status_code == 404:
            send_telegram_message(f"✅ Available: *{username}*\nhttps://www.tiktok.com/@{username}")
    except:
        pass

def checker_loop():
    global running
    while True:
        if not running:
            time.sleep(1)
            continue

        usernames = generate_usernames(100)
        threads = []

        for u in usernames:
            t = threading.Thread(target=check_username, args=(u,))
            t.start()
            threads.append(t)
            while threading.active_count() > MAX_THREADS:
                time.sleep(0.1)
        for t in threads:
            t.join()
        time.sleep(10)

def handle_telegram_updates():
    global running
    last_update_id = None
    while True:
        try:
            resp = requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10)
            updates = resp.json()["result"]
            for update in updates:
                update_id = update["update_id"]
                if last_update_id and update_id <= last_update_id:
                    continue
                last_update_id = update_id

                if "callback_query" in update:
                    data = update["callback_query"]["data"]
                    if data == "start":
                        if not running:
                            running = True
                            send_telegram_message("🟢 Checker started.")
                        else:
                            send_telegram_message("Already running.")
                    elif data == "stop":
                        running = False
                        send_telegram_message("🔴 Checker stopped.")
        except Exception as e:
            print(f"[ERROR] Telegram polling: {e}")
        time.sleep(2)

# --- MAIN ---

if __name__ == "__main__":
    scrape_proxies()
    threading.Thread(target=periodic_proxy_rescrape, daemon=True).start()
    threading.Thread(target=checker_loop, daemon=True).start()
    threading.Thread(target=handle_telegram_updates, daemon=True).start()
    send_telegram_message("Bot online ✅\nUse buttons below to control:", buttons=True)

    while True:
        time.sleep(5)
