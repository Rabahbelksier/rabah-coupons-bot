# AliExpress Telegram Bot

## Overview
Telegram bot that generates affiliate links for AliExpress products. Users send product URLs and receive discount offers with affiliate links. Configured to run on Render.com using Webhook mode with Flask and PostgreSQL.

## Project Structure (Modular Architecture)

```
├── main.py                    # Entry point only: Flask app, Telegram handlers registration, webhook
├── config.py                  # All environment variables and constants
│
├── core/
│   ├── api.py                 # AliExpress API: signature, params, retry requests
│   ├── product.py             # Product data: fetch and parse from API
│   ├── affiliate.py           # Affiliate link generation (batched, ThreadPoolExecutor)
│   ├── scraper.py             # Scraping fallback (BeautifulSoup + regex)
│   ├── cache.py               # TTLCache instances and locks
│   └── db.py                  # PostgreSQL: init_db, save_user
│
├── handlers/
│   ├── start.py               # /start command handler
│   ├── messages.py            # Incoming message handler
│   └── callbacks.py           # Inline button callbacks (product details)
│
├── services/
│   ├── worker.py              # process_link_for_user: full pipeline for one URL
│   └── queue_manager.py       # Per-user asyncio queues, enqueue_url, worker loop
│
└── utils/
    ├── http.py                # Shared requests.Session with connection pooling
    ├── parser.py              # extract_product_id (regex first, HEAD request fallback)
    └── formatter.py           # format_product_message (Markdown output)
```

## Architecture
- **main.py**: Lean Flask entry point — only app setup, handler registration, webhook config
- **API**: AliExpress Affiliate API (`api-sg.aliexpress.com/sync`)
- **Scraping**: BeautifulSoup fallback for product info (cached 3600s)
- **Database**: PostgreSQL via psycopg2 for user storage
- **Deployment**: Render.com with gunicorn

## Flow
1. User sends AliExpress URL → `handlers/messages.py` → `services/queue_manager.py`
2. Queue worker calls `services/worker.py` → `process_link_for_user`
3. `utils/parser.py` extracts product ID (regex first, HEAD request only if needed)
4. `core/product.py` fetches title & image via API concurrently with `core/affiliate.py`
5. Falls back to `core/scraper.py` if API returns no useful info
6. Sends message with product image, title, and 8 offer links
7. User clicks "تفاصيل المنتج الكاملة" → `handlers/callbacks.py` → full product API response

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

## Performance Optimizations
- **ThreadPoolExecutor(5)**: Reduced from 10 → less CPU pressure
- **Batched link generation**: 8 requests split into 2 batches of 4 (no burst)
- **TTLCache**: maxsize=400 / ttl=600s for main cache; maxsize=200 / ttl=3600s for scraping
- **Lock-free reads**: cache reads use no lock (Python GIL safe), writes are locked
- **Direct regex matching**: product ID extracted from URL before any HTTP call
- **Shared HTTP session**: connection pooling with keep-alive reuse
- **Queue cleanup**: user queues deleted after draining to prevent memory leaks
- **Concurrent fetch**: product info + link generation run via asyncio.gather simultaneously

## Recent Changes
- 2026-02-12: Converted from polling to Webhook mode with Flask
- 2026-02-12: Added PostgreSQL database integration for user storage
- 2026-02-12: Added save_user function with ON CONFLICT DO NOTHING for duplicate prevention
- 2026-04-11: Major performance optimization — parallel link generation + concurrent product fetch/links
- 2026-04-16: Performance optimizations: reduced threads, improved cache, batched requests, lock-free reads
- 2026-04-16: Refactored to modular architecture (core/, handlers/, services/, utils/)
