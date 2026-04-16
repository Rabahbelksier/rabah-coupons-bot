import html
import logging
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils.parser import extract_product_id
from core.product import get_product_info_from_api
from core.affiliate import generate_affiliate_links
from core.scraper import get_product_details_scraping

logger = logging.getLogger(__name__)


async def process_link_for_user(chat_id: int, url: str, context):
    bot = context.bot

    product_id = await asyncio.to_thread(extract_product_id, url)
    if not product_id:
        await bot.send_message(chat_id=chat_id, text="❌ انسخ رابط المنتج من تطبيق aliexpress او الموقع")
        return

    loading_msg = await bot.send_message(chat_id=chat_id, text="⏳ جاري البحث عن العروض")

    try:
        product_task = asyncio.to_thread(get_product_info_from_api, product_id)
        links_task = asyncio.to_thread(generate_affiliate_links, product_id)

        product, links = await asyncio.gather(product_task, links_task)

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
