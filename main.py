import os, re, hmac, hashlib, requests, time, json
from datetime import datetime
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from functools import lru_cache
from cachetools import TTLCache

# ----------- إعدادات -----------
APP_KEY, APP_SECRET = os.getenv('APP_KEY'), os.getenv('APP_SECRET')
TRACKING_ID, TOKEN = os.getenv('TRACKING_ID'), os.getenv('TELEGRAM_TOKEN')
API_URL = "https://api-sg.aliexpress.com/sync"

if not all([APP_KEY, APP_SECRET, TRACKING_ID, TOKEN]):
    raise EnvironmentError("❌ Missing required environment variables")

# ----------- كاش مع TTL -----------
cache = TTLCache(maxsize=1000, ttl=300)

# ----------- دوال مساعدة لـ API AliExpress -----------
def send_api_request_with_retry(all_params, max_retries=2):
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, data=all_params, timeout=10)
            if response.status_code != 200:
                if attempt < max_retries - 1: time.sleep(1); continue
            data = response.json()
            if 'error_response' in data:
                if data['error_response'].get('code') == 'ApiCallLimit':
                    ban_time = 5 if '5 seconds' in data['error_response'].get('msg','') else 1
                    if attempt < max_retries - 1: time.sleep(ban_time + 0.5); continue
                return data
            return data
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1: time.sleep(2); continue
        except Exception as e:
            if attempt < max_retries - 1: time.sleep(1); continue
    return {'error_response': {'code': 'MaxRetriesExceeded', 'msg': 'فشلت جميع المحاولات'}}

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

def parse_product_data(product_data):
    try:
        product = product_data['aliexpress_affiliate_productdetail_get_response']['resp_result']['result']['products'].get('product')
        if not product: return {}
        p = product[0] if isinstance(product, list) else product
        sale_price = p.get('target_sale_price', p.get('app_sale_price', 'غير متوفر'))
        original_price = p.get('target_original_price', p.get('original_price', 'غير متوفر'))
        discount = p.get('target_discount', 'غير محسوب')
        if discount == 'غير محسوب' and original_price != 'غير متوفر' and sale_price != 'غير متوفر':
            try:
                original = float(str(original_price).replace('USD', '').replace('$', '').strip())
                sale = float(str(sale_price).replace('USD', '').replace('$', '').strip())
                if original > 0: discount = f"{((original - sale) / original) * 100:.1f}%"
            except: pass
        shop_url = p.get('shop_url', 'غير متوفر')
        if '/store/' in shop_url:
            try: shop_url = f"https://m.aliexpress.com/store/{shop_url.split('/store/')[1].split('/')[0].split('?')[0]}?shopId={shop_url.split('/store/')[1].split('/')[0].split('?')[0]}"
            except: pass
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
    except: return {}

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

# ----------- معالجة الروابط -----------
@lru_cache(maxsize=100)
def extract_product_id(text):
    try:
        if not any(domain in text for domain in ['aliexpress.com', 'alix.live', 's.click.aliexpress.com']):
            return None
        session = requests.Session()
        response = session.head(text, allow_redirects=True, timeout=10)
        final_url = response.url
    except:
        final_url = text

    patterns = [
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

    for pattern in patterns:
        match = re.search(pattern, final_url)
        if match:
            return match.group(1)
    return None

# ----------- توليد التوقيع -----------
def generate_api_signature(params, secret):
    param_string = ''.join([f"{k}{v}" for k, v in sorted(params.items())])
    return hmac.new(secret.encode('utf-8'), param_string.encode('utf-8'), hashlib.sha256).hexdigest().upper()

# ----------- توليد الروابط -----------
def generate_affiliate_links(product_id):
    cache_key = f"links_{product_id}"
    if cache_key in cache:
        return cache[cache_key]

    offers_primary = [
        ("💥عرض المنتج في صفحة العملات", f"https://m.aliexpress.com/p/coin-index/index.html?_immersiveMode=true&productIds={product_id}"),
        ("💥رابط مباشر للمنتج", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=620"),
        ("💥عرض Super Deals", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=562"),
        ("💥عرض تخفيض Big Save", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=680"),
        ("💥عرض التخفيض المحدود", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=561"),
        ("💥عرض التخفيض المحتمل", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=504"),
        ("💥عرض مباشر للباندل ", f"https://www.aliexpress.com/item/{product_id}.html?sourceType=570"),
        ("💥عرض المنتج في صفحة الباندل", f"https://www.aliexpress.com/ssr/300000512/BundleDeals2?&pha_manifest=ssr&productIds={product_id}")
    ]

    offers_secondary = [
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://m.aliexpress.com/p/coin-index/index.html?_immersiveMode=true&productIds={product_id}",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=620",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=562",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=680",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=561",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=504",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/item/{product_id}.html?sourceType=570",
        f"https://star.aliexpress.com/share/share.htm?redirectUrl=https://www.aliexpress.com/ssr/300000512/BundleDeals2?&pha_manifest=ssr&productIds={product_id}",
    ]

    def try_generate_link(url_to_try):
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

        try:
            response = requests.get(API_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            result = data.get('aliexpress_affiliate_link_generate_response', {}).get('resp_result', {}).get('result', {})
            if result.get('promotion_links'):
                return result['promotion_links']['promotion_link'][0]['promotion_link']
        except Exception as e:
            print(f"خطأ في توليد الرابط: {e}")
        return None

    results = []
    for i, (name, primary_url) in enumerate(offers_primary):
        affiliate_link = try_generate_link(primary_url)
        if affiliate_link is None and i < len(offers_secondary):
            secondary_url = offers_secondary[i]
            affiliate_link = try_generate_link(secondary_url)
        if affiliate_link:
            results.append(f"{name}:\n{affiliate_link}")
        else:
            results.append(f"{name}:\n❌ فشل التوليد من المصدر")

    cache[cache_key] = results
    return results

# ----------- معلومات المنتج باستخدام BeautifulSoup -----------
@lru_cache(maxsize=50)
def get_product_details(product_id):
    try:
        url = f"https://www.aliexpress.com/item/{product_id}.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        title = None
        title_element = soup.find('h1', {'class': 'product-title-text'})
        if title_element:
            title = title_element.get_text(strip=True)

        if not title:
            meta_title = soup.find('meta', {'property': 'og:title'})
            if meta_title:
                title = meta_title.get('content', '')

        if not title:
            title_element = soup.find('title')
            if title_element:
                title = title_element.get_text(strip=True)

        image_url = None
        image_element = soup.find('img', {'class': 'magnifier-image'})
        if image_element:
            image_url = image_element.get('src') or image_element.get('data-src')

        if not image_url:
            meta_image = soup.find('meta', {'property': 'og:image'})
            if meta_image:
                image_url = meta_image.get('content')

        if image_url and image_url.startswith('//'):
            image_url = f"https:{image_url}"

        return {
            'title': title.strip()[:255] if title else 'تعذر استخراج العنوان',
            'image_url': image_url
        }
    except Exception as e:
        print(f"خطأ في استخراج معلومات المنتج: {e}")
        return {'title': 'تعذر استخراج العنوان', 'image_url': None}

# ----------- أوامر البوت -----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_text = "⚙️مرحبا بك في بوت التخفيض الخاص بالمتجر الصيني الشهير Aliexpress..."
    await update.message.reply_text(start_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    url_pattern = r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    urls = re.findall(url_pattern, user_input)

    target_url = next((url for url in urls if any(domain in url for domain in ['aliexpress.com', 'alix.live', 's.click.aliexpress.com'])), None)
    if not target_url:
        await update.message.reply_text("⚠️ من فضلك قم بإرسال روابط منتجات Aliexpress فقط 😕")
        return

    try:
        product_id = extract_product_id(target_url)
        if not product_id:
            await update.message.reply_text("❌ انسخ رابط المنتج من تطبيق aliexpress او الموقع")
            return

        await update.message.reply_text("⏳ جاري البحث عن العروض")
        product = get_product_details(product_id)
        links = generate_affiliate_links(product_id)

        keyboard = [[InlineKeyboardButton("📋 تفاصيل المنتج الكاملة", callback_data=f"details_{product_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        response_text = f"📦تخفيض على:\n {product['title']}\n\n" + "\n\n".join(links) if product['title'] != 'تعذر استخراج العنوان' else "📦 تخفيض على منتج AliExpress\n\n" + "\n\n".join(links)

        if product['image_url']:
            try:
                await update.message.reply_photo(photo=product['image_url'], caption=response_text, parse_mode="HTML", reply_markup=reply_markup)
            except:
                await update.message.reply_text(response_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(response_text, reply_markup=reply_markup)

    except Exception as e:
        print(f"خطأ عام في handle_message: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء معالجة طلبك")

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
            await status_message.edit_text(f"❌ خطأ من AliExpress: {data['error_response'].get('msg', 'خطأ غير معروف')}")
            return
            
        info = parse_product_data(data)
        if not info:
            await status_message.edit_text("⚠️ فشل في تحليل بيانات المنتج. قد يكون المنتج غير متاح في نظام الأفلييت.")
            return
            
        await status_message.edit_text(format_product_message(info), parse_mode='Markdown', disable_web_page_preview=True)
        
    except Exception as e:
        print(f"خطأ في callback التفاصيل: {e}")
        await query.message.reply_text("❌ حدث خطأ غير متوقع أثناء جلب التفاصيل.")

# ----------- تشغيل البوت -----------
def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(product_details_callback, pattern="^details_"))
    print("✅ البوت يعمل...")
    application.run_polling()

if __name__ == '__main__':
    main()