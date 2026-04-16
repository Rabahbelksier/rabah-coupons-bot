import os
import re
import hmac
import hashlib
import html
import requests
import time
import logging
import asyncio
import threading
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from bs4 import BeautifulSoup
from cachetools import TTLCache
from flask import Flask, request, Response
from requests.adapters import HTTPAdapter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_KEY = os.getenv('APP_KEY')
APP_SECRET = os.getenv('APP_SECRET')
TRACKING_ID = os.getenv('TRACKING_ID')
TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
PORT = int(os.getenv('PORT', 5000))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
API_URL = "https://api-sg.aliexpress.com/sync"

if not all([APP_KEY, APP_SECRET, TRACKING_ID, TOKEN]):
    raise EnvironmentError("Missing required environment variables")

app = Flask(__name__)

# Reduced maxsize (400 vs 1000) and doubled TTL (600 vs 300) to lower RAM usage
cache = TTLCache(maxsize=400, ttl=600)
cache_lock = threading.Lock()

# Separate cache for scraping results with much longer TTL (3600s) to reduce re-scraping
scrape_cache = TTLCache(maxsize=200, ttl=3600)
scrape_cache_lock = threading.Lock()

# Shared HTTP session with improved connection pooling for better reuse and lower latency
_http_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
_http_session.mount('http://', _adapter)
_http_session.mount('https://', _adapter)
_http_session.headers.update({'Connection': 'keep-alive'})

# Reduced thread pool (5 vs 10) to lower CPU pressure and prevent API burst
_link_executor = ThreadPoolExecutor(max_workers=5)


# ─── Database ────────────────────────────────────────────────────────────────

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set, skipping database initialization")
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_bot (
                first_name TEXT,
                last_name TEXT,
                chat_id BIGINT PRIMARY KEY
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")


def save_user(chat_id, first_name, last_name):
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_bot (chat_id, first_name, last_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO NOTHING
        """, (chat_id, first_name, last_name))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving user: {e}")


# ─── API Helpers ──────────────────────────────────────────────────────────────

def generate_api_signature(params, secret):
    param_string = ''.join([f"{k}{v}" for k, v in sorted(params.items())])
    return hmac.new(secret.encode('utf-8'), param_string.encode('utf-8'), hashlib.sha256).hexdigest().upper()


def prepare_api_params(method, extra_params):
    params = {
        'method': method,
        'app_key': APP_KEY,
        'sign_method': 'sha256',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'format': 'json',
        'v': '2.0',
    }
    params.update(extra_params)
    params['sign'] = generate_api_signature(params, APP_SECRET)
    return params


def send_api_request_with_retry(all_params, max_retries=2):
    for attempt in range(max_retries):
        try:
            response = _http_session.post(API_URL, data=all_params, timeout=8)
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
            data = response.json()
            if 'error_response' in data:
                if data['error_response'].get('code') == 'ApiCallLimit':
                    ban_time = 5 if '5 seconds' in data['error_response'].get('msg', '') else 1
                    if attempt < max_retries - 1:
                        time.sleep(ban_time + 0.5)
                        continue
                return data
            return data
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
    return {'error_response': {'code': 'MaxRetriesExceeded', 'msg': 'فشلت جميع المحاولات'}}


# ─── Product Info ─────────────────────────────────────────────────────────────

def parse_product_data(product_data):
    try:
        product = product_data['aliexpress_affiliate_productdetail_get_response']['resp_result']['result']['products'].get('product')
        if not product:
            return {}
        p = product[0] if isinstance(product, list) else product
        sale_price = p.get('target_sale_price', p.get('app_sale_price', 'غير متوفر'))
        original_price = p.get('target_original_price', p.get('original_price', 'غير متوفر'))
        discount = p.get('target_discount', 'غير محسوب')
        if discount == 'غير محسوب' and original_price != 'غير متوفر' and sale_price != 'غير متوفر':
            try:
                original = float(str(original_price).replace('USD', '').replace('$', '').strip())
                sale = float(str(sale_price).replace('USD', '').replace('$', '').strip())
                if original > 0:
                    discount = f"{((original - sale) / original) * 100:.1f}%"
            except Exception:
                pass
        shop_url = p.get('shop_url', 'غير متوفر')
        if '/store/' in shop_url:
            try:
                store_id = shop_url.split('/store/')[1].split('/')[0].split('?')[0]
                shop_url = f"https://m.aliexpress.com/store/{store_id}?shopId={store_id}"
            except Exception:
                pass
        return {
            'product_title': p.get('product_title', 'غير متوفر'),
            'target_sale_price': f"{sale_price} USD",
            'target_original_price': f"{original_price} USD",
            'target_discount': discount,
            'lastest_volume': p.get('lastest_volume', 'غير متوفر'),
            'shop_name': p.get('shop_name', 'غير متوفر'),
            'evaluate_rate': p.get('evaluate_rate', 'غير متوفر'),
            'shop_url': shop_url,
            'first_level_category_name': p.get('first_level_category_name', 'غير محدد'),
            'second_level_category_name': p.get('second_level_category_name', 'غير محدد'),
            'commission_rate': p.get('commission_rate', 'غير محدد')
        }
    except Exception:
        return {}


def get_product_info_from_api(product_id):
    try:
        params = prepare_api_params('aliexpress.affiliate.productdetail.get', {
            'product_ids': product_id,
            'target_currency': 'USD',
            'target_language': 'EN',
            'tracking_id': TRACKING_ID,
            'fields': 'product_title,product_main_image_url'
        })
        data = send_api_request_with_retry(params, max_retries=2)
        if 'error_response' in data:
            logger.error(f"API error in get_product_info_from_api: {data['error_response'].get('msg', 'unknown')}")
            return None
        product = (
            data.get('aliexpress_affiliate_productdetail_get_response', {})
                .get('resp_result', {})
                .get('result', {})
                .get('products', {})
                .get('product')
        )
        if not product:
            return None
        p = product[0] if isinstance(product, list) else product
        title = p.get('product_title', '')
        image_url = p.get('product_main_image_url', '')
        if image_url and image_url.startswith('//'):
            image_url = f"https:{image_url}"
        if not title and not image_url:
            return None
        return {
            'title': title.strip()[:255] if title else 'غير متوفر',
            'image_url': image_url if image_url else None
        }
    except Exception as e:
        logger.error(f"Error in get_product_info_from_api: {e}")
        return None


def format_product_message(info):
    return f"""📦 **تفاصيل المنتج الكاملة**
🛒 **الاسم:** {info['product_title']}
💰 **السعر الحالي:** {info['target_sale_price']}
🏷️ **السعر الأصلي:** {info['target_original_price']}
🎁 **نسبة الخصم:** {info['target_discount']}
📊 **عدد الطلبات:** {info['lastest_volume']}

🏪 **معلومات المتجر:** 
🏠 **اسم المتجر:** {info['shop_name']}
⭐️ **تقييم المتجر:** {info['evaluate_rate']}
🔗 [رابط المتجر]({info['shop_url']})

📂 **معلومات إضافية:**
   • الفئة الرئيسية: {info['first_level_category_name']}
   • الفئة الفرعية: {info['second_level_category_name']}
💡 **نسبة العمولة:** {info['commission_rate']}

⏰ *تم الاستخراج في: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*"""


# ─── URL / Product ID ─────────────────────────────────────────────────────────

_URL_PATTERNS = [
    r'[?&]productIds=(\d+)',
    r'[?&]productId=(\d+)',
    r'/item/(\d+)\.(?:html|htm)',
    r'/item/(\d+)(?:\?|$)',
    r'/product/(\d+)',
    r'/i/(\d+)',
    r'/p/[^/]+/index\.html[?&]productIds=(\d+)',
    r'/ssr/.*?[?&]productIds=(\d+)',
    r'/[a-z0-9]+\.html\?.*?productId(?:s)?=(\d+)',
]


def _match_product_id_from_url(url):
    for pattern in _URL_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_product_id(text):
    cache_key = f"pid_{text}"

    # Lock-free read — safe under Python GIL for TTLCache get
    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    if not any(domain in text for domain in ['aliexpress.com', 'alix.live', 's.click.aliexpress.com']):
        return None

    # Try to extract product ID directly from the input URL first (no HTTP call needed)
    direct_match = _match_product_id_from_url(text)
    if direct_match:
        with cache_lock:
            cache[cache_key] = direct_match
        return direct_match

    # Only perform HEAD request if direct pattern matching failed
    try:
        response = _http_session.head(text, allow_redirects=True, timeout=8)
        final_url = response.url
    except Exception:
        final_url = text

    result = _match_product_id_from_url(final_url)
    if result:
        with cache_lock:
            cache[cache_key] = result
    return result


# ─── Affiliate Link Generation ────────────────────────────────────────────────

def _generate_single_link(url_to_try, max_retries=2):
    for attempt in range(max_retries):
        try:
            params = {
                "method": "aliexpress.affiliate.link.generate",
                "app_key": APP_KEY,
                "sign_method": "sha256",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "v": "2.0",
                "format": "json",
                "tracking_id": TRACKING_ID,
                "promotion_link_type": "0",
                "source_values": url_to_try
            }
            params['sign'] = generate_api_signature(params, APP_SECRET)

            response = _http_session.get(API_URL, params=params, timeout=8)
            response.raise_for_status()
            data = response.json()

            if 'error_response' in data:
                err = data['error_response']
                code = err.get('code', '')
                msg = err.get('msg', '')
                logger.warning(f"API error on link generation (attempt {attempt + 1}): [{code}] {msg}")
                if code == 'ApiCallLimit':
                    wait = 5.5 if '5 seconds' in msg else 1.5
                    if attempt < max_retries - 1:
                        time.sleep(wait)
                        continue
                return None

            result = (
                data.get('aliexpress_affiliate_link_generate_response', {})
                    .get('resp_result', {})
                    .get('result', {})
            )
            if result.get('promotion_links'):
                return result['promotion_links']['promotion_link'][0]['promotion_link']
            return None

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout generating link (attempt {attempt + 1}): {url_to_try}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
        except Exception as e:
            logger.error(f"Error generating link (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue

    return None


def _generate_one_offer(index, name, primary_url, secondary_url):
    """Generate a single affiliate link, trying primary then secondary URL."""
    affiliate_link = _generate_single_link(primary_url)
    if affiliate_link is None and secondary_url:
        affiliate_link = _generate_single_link(secondary_url)
    if affiliate_link:
        return index, f"{name}:\n{affiliate_link}"
    return index, f"{name}:\n❌ فشل التوليد من المصدر"


def generate_affiliate_links(product_id):
    cache_key = f"links_{product_id}"

    # Lock-free read — safe under Python GIL for TTLCache get
    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    offers = [
        (
            "💥عرض المنتج في صفحة العملات",
            f"https://m.aliexpress.com/p/coin-index/index.html?_immersiveMode=true&tabname=configTab_1926001&productIds={product_id}",
            f"https://m.aliexpress.com/p/coin-index/index.html?_immersiveMode=true&tabname=configTab_1926001&productIds={product_id}",
        ),
        (
            "💥رابط مباشر للمنتج",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=620",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=620",
        ),
        (
            "💥عرض Super Deals",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=562",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=562",
        ),
        (
            "💥عرض تخفيض Big Save",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=680",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=680",
        ),
        (
            "💥عرض التخفيض المحدود",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=561",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=561",
        ),
        (
            "💥عرض التخفيض المحتمل",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=504",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=504",
        ),
        (
            "💥عرض مباشر للباندل ",
            f"https://www.aliexpress.com/item/{product_id}.html?sourceType=570",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=570",
        ),
        (
            "💥عرض المنتج في صفحة الباندل",
            f"https://www.aliexpress.com/ssr/300000512/BundleDeals2?&pha_manifest=ssr&productIds={product_id}",
            f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/ssr/300000512/BundleDeals2?&pha_manifest=ssr&productIds={product_id}",
        ),
    ]

    results_map = {}
    batch_size = 4

    # Process offers in batches to avoid bursting all 8 requests simultaneously
    for batch_start in range(0, len(offers), batch_size):
        batch = offers[batch_start:batch_start + batch_size]
        futures = {
            _link_executor.submit(_generate_one_offer, batch_start + j, name, primary, secondary): batch_start + j
            for j, (name, primary, secondary) in enumerate(batch)
        }
        for future in as_completed(futures):
            try:
                index, text = future.result()
                results_map[index] = text
            except Exception as e:
                idx = futures[future]
                name = offers[idx][0]
                results_map[idx] = f"{name}:\n❌ فشل التوليد من المصدر"
                logger.error(f"Unexpected error for offer {idx}: {e}")

    results = [results_map[i] for i in range(len(offers))]

    with cache_lock:
        cache[cache_key] = results
    return results


# ─── Web Scraping Fallback ────────────────────────────────────────────────────

def get_product_details_scraping(product_id):
    cache_key = f"scrape_{product_id}"

    # Lock-free read from scrape_cache (separate long-lived cache, TTL=3600)
    try:
        cached = scrape_cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    try:
        url = f"https://www.aliexpress.com/item/{product_id}.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Referer": "https://www.aliexpress.com/",
        }

        response = _http_session.get(url, headers=headers, timeout=12)
        response.raise_for_status()

        title = None
        image_url = None

        try:
            script_tags = re.findall(r'<script[^>]*>(.*?)</script>', response.text, re.DOTALL)
            for script in script_tags:
                if not title:
                    title_match = re.search(r'"subject"\s*:\s*"([^"]+)"', script)
                    if title_match:
                        title = title_match.group(1)
                if not image_url:
                    img_match = re.search(r'"imageUrl"\s*:\s*"([^"]+)"', script)
                    if not img_match:
                        img_match = re.search(r'"imagePathList"\s*:\s*\[\s*"([^"]+)"', script)
                    if img_match:
                        image_url = img_match.group(1)
                if title and image_url:
                    break
        except Exception as e:
            logger.error(f"Error parsing JavaScript: {e}")

        if not title or not image_url:
            soup = BeautifulSoup(response.content, 'html.parser')

            if not title:
                meta_title = soup.find('meta', {'property': 'og:title'})
                if meta_title:
                    title = meta_title.get('content', '')

            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    raw_title = title_tag.get_text(strip=True)
                    if raw_title and 'AliExpress' not in raw_title[:10]:
                        title = raw_title.split('|')[0].split('-')[0].strip()

            if not image_url:
                meta_image = soup.find('meta', {'property': 'og:image'})
                if meta_image:
                    image_url = meta_image.get('content')

            if not image_url:
                for img in soup.find_all('img'):
                    src = img.get('src') or img.get('data-src') or ''
                    if 'ae01.alicdn.com' in src or 'cbu01.alicdn.com' in src:
                        image_url = src
                        break

        if image_url and image_url.startswith('//'):
            image_url = f"https:{image_url}"

        result = {
            'title': title.strip()[:255] if title else None,
            'image_url': image_url
        }
        if result.get('title') or result.get('image_url'):
            with scrape_cache_lock:
                scrape_cache[cache_key] = result
        return result
    except Exception as e:
        logger.error(f"Error in scraping: {e}")
        return {'title': None, 'image_url': None}


# ─── Telegram Handlers ────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.first_name, user.last_name or '')
    start_text = "⚙️مرحبا بك في بوت التخفيض الخاص بالمتجر الصيني الشهير Aliexpress \n  \n ✅خصائص البوت:  \n ●توفير روابط عروض باسعار لاتجدها على الموقع \n ●له القدرة على جلب ست عروض مختلفة \n ●لذيه امكانية سحب ومعاينة السعر النهائي للمنتج \n ●يمكنه الحصول على نسبة التخفيض بالعملات وطبيعة الشحن \n ●يمكنه معرفة المنتج ان كان DDP ام لا (خاص بالاخوة المغاربة) \n ●امكانية الحصول على السعر بعملتي الدولار والدينار الجزائري \n ●معرفة عدد مبيعات المنتج وتقييمه بالاضافة الى تقييم المتجر \n  \n ✅طريقة استعمال البوت:  \n بطريقة بسيطة قم بنسخ رابط المنتج المراد شراؤه من موقع Aliexress من خلال رمز المشاركة الموجود في الاعلى (بالنسبة للتطبيق)، بعدها قم بلصق الرابط وارساله الى البوت، الان عليك النتظار ثواني قليلة ليقوم البوت بالتحضير والبحث عن العروض الممكنة، بعد حصولك على العروض اختر احسن سعر وقم باتمام عملية الشراء \n  \n ⛔ملاحظة: قم بتغيير الدولة في التطبيق (او الحساب)  إلى دولة كندا أو دولة اخرى أجنبية، مع ترك عنوان الشحن الخاص بك كما هو، وذالك للحصول على نسبة تخفيض أعلى في عرض العملات \n اي جديد تجدونه على قناتنا @rabahcopons"
    await update.message.reply_text(start_text)


async def product_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.split('_')[1]

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        status_message = await query.message.reply_text("⏳ جاري جلب التفاصيل الكاملة من AliExpress...")

        params = prepare_api_params('aliexpress.affiliate.productdetail.get', {
            'product_ids': product_id,
            'target_currency': 'USD',
            'target_language': 'EN',
            'tracking_id': TRACKING_ID
        })

        data = send_api_request_with_retry(params, max_retries=3)

        if 'error_response' in data:
            await status_message.edit_text(f"❌ خطأ من AliExpress: {data['error_response'].get('msg', 'خطأ غير معروف اتصل بالأدمن @Rabahbelksier')}")
            return

        info = parse_product_data(data)
        if not info:
            await status_message.edit_text("⚠️ لا يمكن جلب تفاصيل هذا المنتج")
            return

        await status_message.edit_text(format_product_message(info), parse_mode='Markdown', disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error in details callback: {e}")
        await query.message.reply_text("❌ حدث خطأ غير متوقع أثناء جلب التفاصيل.")


async def process_link_for_user(chat_id: int, url: str, context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot

    product_id = await asyncio.to_thread(extract_product_id, url)
    if not product_id:
        await bot.send_message(chat_id=chat_id, text="❌ انسخ رابط المنتج من تطبيق aliexpress او الموقع")
        return

    loading_msg = await bot.send_message(chat_id=chat_id, text="⏳ جاري البحث عن العروض")

    try:
        # Run product info fetch and affiliate link generation concurrently
        product_task = asyncio.to_thread(get_product_info_from_api, product_id)
        links_task = asyncio.to_thread(generate_affiliate_links, product_id)

        product, links = await asyncio.gather(product_task, links_task)

        # If the API returned no useful product info, fall back to scraping
        if not product or (not product.get('title') or product.get('title') == 'غير متوفر'):
            logger.info(f"API returned insufficient data for {product_id}, trying scraping...")
            scraped = await asyncio.to_thread(get_product_details_scraping, product_id)
            if not product:
                product = {'title': None, 'image_url': None}
            if scraped.get('title'):
                product['title'] = (
                    product.get('title')
                    if product.get('title') and product.get('title') != 'غير متوفر'
                    else scraped['title']
                )
            if scraped.get('image_url') and not product.get('image_url'):
                product['image_url'] = scraped['image_url']

        title = product.get('title') if product and product.get('title') else None
        image_url = product.get('image_url') if product else None

        keyboard = [[InlineKeyboardButton("📋 تفاصيل المنتج الكاملة", callback_data=f"details_{product_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if title:
            response_text = f"📦تخفيض على:\n<code>{html.escape(title)}</code>\n\n" + "\n\n".join(links)
        else:
            response_text = "📦 تخفيض على منتج AliExpress\n\n" + "\n\n".join(links)

        if image_url:
            try:
                await bot.send_photo(chat_id=chat_id, photo=image_url, caption=response_text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as photo_err:
                logger.error(f"Failed to send photo: {photo_err}")
                await bot.send_message(chat_id=chat_id, text=response_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id=chat_id, text=response_text, parse_mode="HTML", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error processing link {url}: {e}")
        await bot.send_message(chat_id=chat_id, text="❌ حدث خطأ أثناء معالجة طلبك، اتصل بالأدمن @Rabahbelksier")

    finally:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=loading_msg.message_id)
        except Exception:
            pass


user_queues: dict = {}
active_workers: set = set()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    chat_id = update.effective_chat.id
    url_pattern = r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    urls = re.findall(url_pattern, user_input)

    target_url = next(
        (url for url in urls if any(domain in url for domain in ['aliexpress.com', 'alix.live', 's.click.aliexpress.com'])),
        None
    )
    if not target_url:
        await update.message.reply_text("⚠️ من فضلك قم بإرسال روابط منتجات Aliexpress فقط 😕")
        return

    if chat_id not in user_queues:
        user_queues[chat_id] = asyncio.Queue()

    await user_queues[chat_id].put(target_url)

    if chat_id not in active_workers:
        active_workers.add(chat_id)
        asyncio.create_task(user_queue_worker(chat_id, context))


async def user_queue_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    queue = user_queues.get(chat_id)
    if queue is None:
        active_workers.discard(chat_id)
        return
    try:
        while not queue.empty():
            url = queue.get_nowait()
            await process_link_for_user(chat_id, url, context)
            queue.task_done()
    finally:
        active_workers.discard(chat_id)
        # Clean up queue to prevent memory leaks from inactive users
        if chat_id in user_queues and user_queues[chat_id].empty():
            del user_queues[chat_id]


# ─── Flask / Webhook ──────────────────────────────────────────────────────────

telegram_app = Application.builder().token(TOKEN).updater(None).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
telegram_app.add_handler(CallbackQueryHandler(product_details_callback, pattern="^details_"))

_loop = None
_initialized = False
_init_lock = threading.Lock()


def _ensure_ready():
    global _loop, _initialized
    if _initialized:
        return _loop
    with _init_lock:
        if _initialized:
            return _loop
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
        f = asyncio.run_coroutine_threadsafe(telegram_app.initialize(), _loop)
        f.result(timeout=30)
        f = asyncio.run_coroutine_threadsafe(telegram_app.start(), _loop)
        f.result(timeout=30)
        _initialized = True
        logger.info("Telegram app ready in worker process")
        return _loop


@app.route('/')
def index():
    return 'Bot is running'


@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        loop = _ensure_ready()
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, telegram_app.bot)
        asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update), loop
        )
        return Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return Response(status=200)


def set_webhook():
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{TOKEN}"
        url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"
        response = requests.get(url)
        logger.info(f"Webhook set response: {response.json()}")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set, webhook not configured")


init_db()
set_webhook()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
