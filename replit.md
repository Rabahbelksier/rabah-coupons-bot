# AliExpress Telegram Bot

## Overview
Telegram bot that generates affiliate links for AliExpress products. Users send product URLs and receive discount offers with affiliate links.

## Architecture
- **main.py**: Single-file bot using `python-telegram-bot` library
- **API**: AliExpress Affiliate API (`api-sg.aliexpress.com/sync`)
- **Scraping**: BeautifulSoup fallback for product info

## Key Functions
- `get_product_info_from_api(product_id)`: Primary method - fetches title & image via AliExpress API
- `get_product_details_scraping(product_id)`: Secondary/fallback - scrapes product page for title & image
- `parse_product_data(data)`: Parses full product details from API response (used in details callback)
- `generate_affiliate_links(product_id)`: Generates affiliate links for various offer types
- `extract_product_id(url)`: Extracts product ID from various AliExpress URL formats

## Flow
1. User sends AliExpress URL
2. Bot extracts product ID
3. Fetches title & image via API (primary), falls back to scraping if needed
4. Generates affiliate links
5. Sends message with product image, title, and offer links
6. User can click "تفاصيل المنتج الكاملة" for full details via API

## Environment Variables
- `APP_KEY`: AliExpress API key
- `APP_SECRET`: AliExpress API secret
- `TRACKING_ID`: Affiliate tracking ID
- `TELEGRAM_TOKEN`: Telegram bot token

## Recent Changes
- 2026-02-12: Added `get_product_info_from_api` for fetching title/image via API
- 2026-02-12: Fixed and improved scraping function (renamed to `get_product_details_scraping`), made it secondary
- 2026-02-12: Updated `handle_message` to use API first, scraping as fallback, display title & image with offers
