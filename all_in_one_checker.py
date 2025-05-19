import requests
import threading
import time
import random
import re
import json

# Telegram Bot info
TELEGRAM_BOT_TOKEN = '7527264620:AAGG5qpYqV3o0h0NidwmsTOKxqVsmRIaX1A'
TELEGRAM_CHAT_ID = '7755395640'
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

def send_telegram_message(message, buttons=False, callback_query_id=None):
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
        resp = requests.post(url, data=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Telegram error] {e}")

    # If this was a callback query, answer it so Telegram knows we received it
    if callback_query_id:
        try:
            requests.post(f"{TELEGRAM_API}/answerCallbackQuery", data={'callback_query_id': callback_query_id})
        except Exception as e:
            print(f"[Telegram callback error] {e}")

def scrape_proxies():
    global proxy_pool, proxy_usage
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
    new_proxies = set()
    for url in sources:
        try:
            resp = requests.get(url, timeout=10)
            text = resp.text
            found = re.findall(r'(\d{1,3}(?:\.\d{1,3}){3}:\d+)', text)
            for proxy in found:
                new_proxies.add(f"http://{proxy}")
        except:
            pass
        time.sleep(2)

    print(f"[INFO] Validating {len(new_proxies)} proxies...")
    valid_proxies = []
    lock = threading.Lock()

    def validate(proxy):
        try:
            headers = {'User-Agent': random.choice(HEADERS_LIST)}
            r = requests.get("https://www.tiktok.com", proxies={"http": proxy, "https": proxy}, headers=headers, timeout=5)
            if r.status_code == 200:
                with lock:
                    valid_proxies.append(proxy)
        except:
            pass

    threads = []
    for proxy in new_proxies:
        t = threading.Thread(target=validate, args=(proxy,))
        t.start()
        threads.append(t)
        if len(threads) >= 50:
            for t in threads:
                t.join()
            threads = []
    for t in threads:
        t.join()

    with proxy_lock:
        proxy_pool = valid_proxies
        proxy_usage.clear()
    print(f"[INFO] {len(valid_proxies)} proxies are valid and ready.")

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
            updates = resp.json().get("result", [])
            for update in updates:
                update_id = update["update_id"]
                if last_update_id and update_id <= last_update_id:
                    continue
                last_update_id = update_id

                # Handle callback query (button presses)
                if "callback_query" in update:
                    data = update["callback_query"]["data"]
                    callback_id = update["callback_query"]["id"]
                    if data == "start":
                        if not running:
                            running = True
                            send_telegram_message("🟢 Checker started.", callback_query_id=callback_id)
                        else:
                            send_telegram_message("✅ Already running.", callback_query_id=callback_id)
                    elif data == "stop":
                        if running:
                            running = False
                            send_telegram_message("🔴 Checker stopped.", callback_query_id=callback_id)
                        else:
                            send_telegram_message("ℹ️ Already stopped.", callback_query_id=callback_id)
        except Exception as e:
            print(f"[ERROR] Telegram polling: {e}")
        time.sleep(2)

if __name__ == "__main__":
    scrape_proxies()
    threading.Thread(target=periodic_proxy_rescrape, daemon=True).start()
    threading.Thread(target=checker_loop, daemon=True).start()
    threading.Thread(target=handle_telegram_updates, daemon=True).start()

    send_telegram_message("Bot online ✅\nUse buttons below to control:", buttons=True)

    while True:
        time.sleep(5)
