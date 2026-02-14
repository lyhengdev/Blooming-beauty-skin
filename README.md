# Blooming Beauty POS (Flask + Google Sheets)

A lightweight POS app with:
- Product catalog and category filtering
- Cart + checkout flow
- Admin dashboard for inventory and orders
- Invoice printing/emailing
- Google Sheets as backend storage

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment template and configure values:
   ```bash
   cp .env.example .env
   ```
4. Run the app:
   ```bash
   python app.py
   ```
5. Open `http://localhost:5000`.

## Environment Variables

- `SECRET_KEY`: Flask session signing key (required in production)
- `ADMIN_PASSWORD`: admin login password
- `SESSION_COOKIE_SECURE`: set `1` in HTTPS environments
- `GOOGLE_SHEETS_CREDENTIALS`:
  - Local: path to service-account JSON file
  - Render: JSON content (string) of service-account credentials
- `SPREADSHEET_NAME`: Google Sheets document name
- `PRODUCT_CACHE_TTL_SECONDS`: in-memory product cache TTL (default: `10`)
- `EMAIL_ADDRESS` / `EMAIL_PASSWORD`: optional SMTP credentials for invoice email

## Security Notes

- Never commit `.env` or service-account key files.
- Rotate any leaked service-account keys before deploying.
- Use a strong `ADMIN_PASSWORD` and long random `SECRET_KEY`.

## Deployment

This project is compatible with `gunicorn` and includes `runtime.txt` for platform builds.

## Health Check

- `GET /health` returns service status and Google Sheets connectivity.
