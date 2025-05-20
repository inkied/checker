import aiohttp
import asyncio
import random
import re
import json
import os

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HEADERS_LIST = [
    # Add your User-Agent strings here
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64)...',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64)...'
]

proxy_pool = []
proxy_usage = {}
proxy_lock = asyncio.Lock()
running = False
last_update_id = None
MAX_CONCURRENT_CHECKS = 30

async def send_telegram_message(session, text, buttons=False):
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown'
    }
    if buttons:
        payload['reply_markup'] = json.dumps({
            'inline_keyboard': [[
                {'text': 'Start', 'callback_data': 'start'},
                {'text': 'Stop', 'callback_data': 'stop'},
                {'text': 'Proxies', 'callback_data': 'proxies'},
                {'text': 'Rescrape', 'callback_data': 'rescrape'}
            ]]
        })
    try:
        await session.post(f"{TELEGRAM_API}/sendMessage", data=payload, timeout=10)
    except Exception as e:
        print(f"[Telegram send error] {e}")

async def scrape_proxies(session):
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
            async with session.get(url, timeout=10) as resp:
                text = await resp.text()
                found = re.findall(r'(\d{1,3}(?:\.\d{1,3}){3}:\d+)', text)
                for proxy in found:
                    new_proxies.add(f"http://{proxy}")
        except:
            pass
        await asyncio.sleep(2)

    total = len(new_proxies)
    await send_telegram_message(session, f"Validating {total} proxies")

    valid_proxies = []
    semaphore = asyncio.Semaphore(30)

    async def validate(proxy):
        async with semaphore:
            try:
                headers = {'User-Agent': random.choice(HEADERS_LIST)}
                proxy_url = proxy
                async with session.get("https://www.tiktok.com", proxy=proxy_url, headers=headers, timeout=3) as r:
                    if r.status == 200:
                        valid_proxies.append(proxy)
            except:
                pass

    tasks = [validate(proxy) for proxy in new_proxies]
    await asyncio.gather(*tasks)

    async with proxy_lock:
        proxy_pool = valid_proxies
        proxy_usage.clear()

    await send_telegram_message(session, f"Proxy validation complete. Valid proxies: {len(proxy_pool)}", buttons=True)

async def get_proxy():
    async with proxy_lock:
        if not proxy_pool:
            return None
        sorted_proxies = sorted(proxy_pool, key=lambda p: proxy_usage.get(p, 0))
        proxy = sorted_proxies[0]
        proxy_usage[proxy] = proxy_usage.get(proxy, 0) + 1
        return proxy

async def get_proxy_health():
    async with proxy_lock:
        total = len(proxy_pool)
        usable = sum(1 for p in proxy_pool if proxy_usage.get(p, 0) < 5)
    health_pct = int((usable / total) * 100) if total > 0 else 0
    return total, usable, health_pct

def generate_usernames(count=100):
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    usernames = set()
    while len(usernames) < count:
        length = random.choice([4,5,6])
        username = ''.join(random.choice(chars) for _ in range(length))
        usernames.add(username)
    return list(usernames)

async def check_username(session, username):
    proxy = await get_proxy()
    if proxy is None:
        return
    headers = {'User-Agent': random.choice(HEADERS_LIST)}
    url = f"https://www.tiktok.com/@{username}"
    try:
        async with session.get(url, headers=headers, proxy=proxy, timeout=10) as r:
            if r.status == 404:
                await send_telegram_message(session, f"Available: *{username}*\nhttps://www.tiktok.com/@{username}")
    except:
        pass

async def checker_loop():
    global running
    async with aiohttp.ClientSession() as session:
        while True:
            if not running:
                await asyncio.sleep(1)
                continue
            usernames = generate_usernames(100)
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

            async def sem_check(un):
                async with semaphore:
                    await check_username(session, un)

            await asyncio.gather(*(sem_check(u) for u in usernames))
            await asyncio.sleep(10)

async def handle_telegram_updates():
    global running, last_update_id
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                params = {'timeout': 10, 'offset': last_update_id + 1 if last_update_id else None}
                async with session.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=15) as resp:
                    data = await resp.json()
                    for update in data.get('result', []):
                        last_update_id = update['update_id']

                        if 'message' in update:
                            msg = update['message']
                            text = msg.get('text', '').lower()
                            chat_id = msg['chat']['id']

                            if text == '/start':
                                await send_telegram_message(session, "Checker is online.\nUse the buttons below for commands.", buttons=True)
                            elif text == '/stop':
                                running = False
                                await send_telegram_message(session, "Checker stopped.", buttons=True)
                            elif text == '/proxies':
                                total, usable, health_pct = await get_proxy_health()
                                msg_text = (f"Proxy Health\n"
                                            f"Total Valid Proxies: {total}\n"
                                            f"Usable Proxies (<5 uses): {usable}\n"
                                            f"Health: {health_pct}%")
                                await send_telegram_message(session, msg_text, buttons=True)

                        if 'callback_query' in update:
                            data = update['callback_query']['data']

                            if data == 'start':
                                if not running:
                                    running = True
                                    await send_telegram_message(session, "Checker started.")
                                else:
                                    await send_telegram_message(session, "Already running.")
                            elif data == 'stop':
                                running = False
                                await send_telegram_message(session, "Checker stopped.")
                            elif data == 'proxies':
                                total, usable, health_pct = await get_proxy_health()
                                msg_text = (f"Proxy Health\n"
                                            f"Total Valid Proxies: {total}\n"
                                            f"Usable Proxies (<5 uses): {usable}\n"
                                            f"Health: {health_pct}%")
                                await send_telegram_message(session, msg_text, buttons=True)
                            elif data == 'rescrape':
                                await send_telegram_message(session, "Starting proxy rescrape and validation. This may take a moment...")
                                # Run scrape_proxies without blocking
                                asyncio.create_task(scrape_proxies(session))
            except Exception as e:
                print(f"[ERROR] Telegram polling: {e}")

            await asyncio.sleep(2)

async def periodic_proxy_rescrape(session):
    while True:
        await asyncio.sleep(600)
        await scrape_proxies(session)

async def main():
    async with aiohttp.ClientSession() as session:
        await send_telegram_message(session, "Checker is online.\nUse the buttons below for commands.", buttons=True)
        await scrape_proxies(session)
        # Launch background tasks
        asyncio.create_task(periodic_proxy_rescrape(session))
        asyncio.create_task(checker_loop())
        await handle_telegram_updates()

if __name__ == "__main__":
    asyncio.run(main())
