import logging
from telegram import Update
from telegram.ext import ContextTypes

from config import TRACKING_ID
from core.api import prepare_api_params, send_api_request_with_retry
from core.product import parse_product_data
from utils.formatter import format_product_message

logger = logging.getLogger(__name__)


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
