import os
import asyncio
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import html
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from telegram import Bot
from google import genai

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

DB_FILE = "sent_tweets_ai.txt"

TWITTER_ACCOUNTS = [
    "binance",          # Binance
    "Bybit_Official",   # Bybit
    "kucoincom",        # KuCoin
    "MEXC_Official",    # MEXC
    "gate_io",          # Gate.io
    "bitgetglobal",     # Bitget
    "OKX",              # OKX
    "BingXOfficial",    # BingX
    "Krakenfx",         # Kraken
    "Coinbase"          # Coinbase
]

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.moomoo.me",
    "https://nitter.perennialte.ch"
]

bot = Bot(token=TELEGRAM_BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def load_sent_tweets():
    if not os.path.exists(DB_FILE):
        return set()
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_sent_tweets(links):
    with open(DB_FILE, 'a', encoding='utf-8') as f:
        for link in links:
            f.write(link + '\n')

async def fetch_rss_with_retry(account):
    instances = NITTER_INSTANCES.copy()
    random.shuffle(instances)
    for instance in instances:
        try:
            nitter_url = f"{instance}/{account}/rss"
            req = urllib.request.Request(nitter_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            loop = asyncio.get_running_loop()
            response_data = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10).read())
            return response_data
        except Exception:
            continue
    return None

async def check_single_account(account, sent_tweets, today_date):
    tweets_found = []
    try:
        response_data = await fetch_rss_with_retry(account)
        if not response_data:
            return tweets_found

        root = ET.fromstring(response_data)
        items = root.findall('.//item')[:3]
        
        for item in items:
            title = item.find('title').text if item.find('title') is not None else ""
            tweet_link = item.find('link').text if item.find('link') is not None else ""
            pub_date_text = item.find('pubDate').text if item.find('pubDate') is not None else ""
            
            clean_link = tweet_link
            for inst in NITTER_INSTANCES:
                domain = inst.replace("https://", "")
                if domain in clean_link:
                    clean_link = clean_link.replace(domain, "x.com")
                    break

            if pub_date_text:
                try:
                    tweet_datetime = parsedate_to_datetime(pub_date_text)
                    if tweet_datetime.date() != today_date:
                        continue
                except Exception:
                    pass
            
            if clean_link in sent_tweets:
                continue
            
            if title:
                tweets_found.append({
                    'account': account,
                    'text': title,
                    'link': clean_link
                })
    except Exception as e:
        print(f"Error reading @{account}: {e}")
    return tweets_found

async def analyze_all_with_gemini(tweets):
    """تحلیل و فیلتر کل توییت‌ها به صورت یک‌جا با فقط ۱ درخواست به جمنای"""
    if not tweets:
        return []

    formatted_tweets = ""
    for i, t in enumerate(tweets):
        formatted_tweets += f"ID: {i}\nExchange: @{t['account']}\nText: {t['text']}\n-------\n"

    prompt = f"""
    You are an expert crypto trading assistant. I will give you a list of recent tweets from major exchanges.
    Your task is to detect NEW SPOT LISTINGS.
    
    CRITICAL FILTERS:
    - Completely IGNORE Futures, Perpetual, Margin, Leverage, Campaigns, AMA, Maintainance, Giveaways, or standard market updates.
    - It MUST be a brand new token/coin being added to the SPOT market for trading.

    Here is the list of tweets:
    {formatted_tweets}

    For each tweet that is a valid NEW SPOT LISTING, reply EXACTLY in this format (separated by '===' between listings). If none match, reply with "NONE":
    
    MATCHED_ID: [Put the ID number here]
    🚨 **لیستینگ جدید اسپات (Spot Listing)**
    🏦 **صرافی:** [Exchange Name]
    🪙 **نام توکن/رمزارز:** [Token Name or Ticker]
    💵 **جفت‌ارزها:** [Pairs like ABC/USDT or Not Specified]
    ⏰ **زمان شروع معامله:** [Trading time if mentioned, else write ذکر نشده]
    📝 **ترجمه متن توییت:** [Short translation in Persian]
    """

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "NONE"

async def main_pipeline():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Gathering tweets from 10 exchanges...")
    sent_tweets = load_sent_tweets()
    today_date = datetime.now(timezone.utc).date()
    
    # ۱. جمع‌آوری تمام توییت‌های جدید صرافی‌ها به صورت موازی و سریع
    tasks = [check_single_account(account, sent_tweets, today_date) for account in TWITTER_ACCOUNTS]
    results = await asyncio.gather(*tasks)
    
    all_new_tweets = []
    for res in results:
        all_new_tweets.extend(res)
        
    if not all_new_tweets:
        print("No new tweets found in this slot.")
        return

    print(f"Found {len(all_new_tweets)} new tweets. Analyzing all in ONE Gemini call...")
    
    # ۲. ارسال کل توییت‌ها در یک درخواست واحد به جمنای
    gemini_analysis = await analyze_all_with_gemini(all_new_tweets)
    
    links_to_save = [t['link'] for t in all_new_tweets]
    
    if "NONE" in gemini_analysis or len(gemini_analysis) < 15:
        print("No spot listings detected by Gemini.")
        save_sent_tweets(links_to_save)
        return

    # ۳. تفکیک پاسخ جمنای و ارسال موارد تایید شده به تلگرام
    blocks = gemini_analysis.split("===")
    for block in blocks:
        if "MATCHED_ID:" in block:
            try:
                # پیدا کردن ID برای استخراج لینک مرجع
                id_line = [line for line in block.split('\n') if "MATCHED_ID:" in line][0]
                matched_id = int(id_line.split(":")[1].strip())
                original_tweet = all_new_tweets[matched_id]
                
                # حذف خط شناسه برای زیباتر شدن پیام
                clean_report = block.replace(id_line, "").strip()
                
                safe_original_text = html.escape(original_tweet['text'])
                final_message = (
                    f"🤖 **[شکارچی لیستینگ]**\n\n"
                    f"{clean_report}\n\n"
                    f"🇬🇧 **متن انگلیسی توییتر:**\n`{safe_original_text}`\n\n"
                    f"🔗 <a href='{original_tweet['link']}'>لینک اعلامیه در X</a>"
                )
                
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=final_message, parse_mode="HTML")
                print(f"[+] Listing report sent for {original_tweet['account']}")
            except Exception as tg_err:
                print(f"Error parsing block or sending telegram: {tg_err}")

    # ذخیره تمام توییت‌ها در تاریخچه تا در نوبت بعدی تکراری لود نشوند
    save_sent_tweets(links_to_save)
    print("Pipeline execution finished successfully.")

if __name__ == "__main__":
    asyncio.run(main_pipeline())
