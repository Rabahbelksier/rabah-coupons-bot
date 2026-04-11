# AliExpress Telegram Bot

## Overview
Telegram bot that generates affiliate links for AliExpress products. Users send product URLs and receive discount offers with affiliate links. Configured to run on Render.com using Webhook mode with Flask and PostgreSQL.

## Architecture
- **main.py**: Flask web app with Telegram Webhook integration
- **API**: AliExpress Affiliate API (`api-sg.aliexpress.com/sync`)
- **Scraping**: BeautifulSoup fallback for product info
- **Database**: PostgreSQL via psycopg2 for user storage
- **Deployment**: Render.com (free plan) with gunicorn

## Key Functions
- `get_product_info_from_api(product_id)`: Primary method - fetches title & image via AliExpress API
- `get_product_details_scraping(product_id)`: Secondary/fallback - scrapes product page for title & image
- `parse_product_data(data)`: Parses full product details from API response (used in details callback)
- `generate_affiliate_links(product_id)`: Generates affiliate links for various offer types
- `extract_product_id(url)`: Extracts product ID from various AliExpress URL formats
- `save_user(chat_id, first_name, last_name)`: Saves user data to PostgreSQL on /start
- `init_db()`: Creates user_bot table if not exists
- `set_webhook()`: Configures Telegram webhook URL

## Flow
1. User sends AliExpress URL
2. Bot extracts product ID
3. Fetches title & image via API (primary), falls back to scraping if needed
4. Generates affiliate links
5. Sends message with product image, title, and offer links
6. User can click "تفاصيل المنتج الكاملة" for full details via API
7. On /start command, user info saved to PostgreSQL

## Environment Variables
- `APP_KEY`: AliExpress API key
- `APP_SECRET`: AliExpress API secret
- `TRACKING_ID`: Affiliate tracking ID
- `TELEGRAM_TOKEN`: Telegram bot token
- `DATABASE_URL`: PostgreSQL connection string
- `PORT`: Server port (default 5000)
- `RENDER_EXTERNAL_URL`: Render external URL for webhook setup

## Database Schema
- **user_bot** table: first_name (TEXT), last_name (TEXT), chat_id (BIGINT PRIMARY KEY)

## Deployment Files
- `requirements.txt`: Python dependencies
- `Procfile`: `web: gunicorn main:app`

## Performance
- **Parallel link generation**: All 8 affiliate link API calls run concurrently via `ThreadPoolExecutor` (was sequential with 0.4s sleep between each call)
- **Concurrent product fetch + link generation**: `get_product_info_from_api` and `generate_affiliate_links` run simultaneously via `asyncio.gather`
- **Shared HTTP session**: Single `requests.Session` with connection pool (`pool_maxsize=50`) reused across all calls — eliminates per-request connection overhead
- **Reduced timeouts**: API calls 8s (was 10-15s), scraping 12s (was 20s)
- **Scraping only blocks if API fails**: Links are always generated in parallel, scraping runs after if needed

## Recent Changes
- 2026-02-12: Converted from polling to Webhook mode with Flask
- 2026-02-12: Added PostgreSQL database integration for user storage
- 2026-02-12: Created Procfile and updated requirements.txt for Render.com deployment
- 2026-02-12: Added save_user function with ON CONFLICT DO NOTHING for duplicate prevention
- 2026-04-11: Major performance optimization — parallel link generation + concurrent product fetch/links
