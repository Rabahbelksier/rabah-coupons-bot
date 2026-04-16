import re
import logging
from telegram import Update
from telegram.ext import ContextTypes

from services.queue_manager import enqueue_url

logger = logging.getLogger(__name__)


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

    await enqueue_url(chat_id, target_url, context)
