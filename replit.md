# AliExpress Telegram Affiliate Bot

## Overview

This is a Telegram bot that converts AliExpress product links into affiliate links. The bot integrates with the AliExpress Affiliate API to fetch product details and generate tracking links for affiliate marketing purposes.

**Core Functionality:**
- Receives AliExpress product URLs from users via Telegram
- Extracts product IDs and fetches product details from AliExpress API
- Returns product information with affiliate tracking links
- Implements caching to reduce API calls and handle rate limits

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Application Structure

**Pattern:** Single-file Python application with modular functions

The bot follows a straightforward architecture:
1. **Bot Layer** - Telegram bot using python-telegram-bot library handles user interactions
2. **API Integration Layer** - Functions that communicate with AliExpress Affiliate API
3. **Caching Layer** - TTLCache for temporary storage of API responses

### Key Components

**Telegram Bot Handler:**
- Uses python-telegram-bot async framework (v20.7)
- Handles commands and message processing
- Supports inline keyboard interactions

**AliExpress API Integration:**
- Communicates with `https://api-sg.aliexpress.com/sync` endpoint
- Uses HMAC-SHA256 for API request signing
- Implements retry logic with exponential backoff for rate limiting
- Handles `ApiCallLimit` errors with appropriate delays

**Caching Strategy:**
- Uses `cachetools.TTLCache` with 5-minute TTL
- Caches up to 1000 items to reduce redundant API calls

### Authentication Flow

**AliExpress API:**
- Requires APP_KEY, APP_SECRET, and TRACKING_ID
- Each request is signed using HMAC-SHA256
- Signature includes sorted parameters concatenated with timestamps

**Environment Variables Required:**
- `APP_KEY` - AliExpress API application key
- `APP_SECRET` - AliExpress API secret for signing requests
- `TRACKING_ID` - Affiliate tracking identifier
- `TELEGRAM_TOKEN` - Telegram Bot API token

### Data Processing

**URL Parsing:**
- Extracts product IDs from AliExpress URLs (pattern: `/item/{product_id}.html`)
- Supports various AliExpress URL formats

**Product Data:**
- Fetches pricing (sale price, original price)
- Retrieves product details in specified currency (USD) and language (EN)

## External Dependencies

### Third-Party Services

| Service | Purpose | Configuration |
|---------|---------|---------------|
| Telegram Bot API | User interface and messaging | `TELEGRAM_TOKEN` |
| AliExpress Affiliate API | Product data and affiliate links | `APP_KEY`, `APP_SECRET`, `TRACKING_ID` |

### Python Libraries

| Library | Version | Purpose |
|---------|---------|---------|
| python-telegram-bot | 20.7 | Telegram bot framework |
| requests | 2.31.0 | HTTP client for API calls |
| beautifulsoup4 | 4.12.2 | HTML parsing (if needed for scraping) |
| lxml | 4.9.3 | XML/HTML parser backend |
| cachetools | 5.3.2 | TTL-based caching |
| apscheduler | 3.10.4 | Task scheduling |

### API Endpoints

**AliExpress API:**
- Base URL: `https://api-sg.aliexpress.com/sync`
- Method: `aliexpress.affiliate.productdetail.get`
- Response format: JSON