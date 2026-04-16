from datetime import datetime


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
