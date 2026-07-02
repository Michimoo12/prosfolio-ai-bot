"""
rates.py
Gets the USD -> PHP exchange rate.

Order of preference:
 1. A manual rate you set with /setrate today (stored in the Settings tab).
 2. The live market rate fetched once per day from a free, no-key service.
 3. The fallback rate in your .env (only if the internet/service is down).
"""

import requests

import config

LIVE_URL = "https://open.er-api.com/v6/latest/USD"  # free, no API key required


def fetch_live_rate():
    """Return today's USD->PHP rate from the free service, or None if it fails."""
    try:
        r = requests.get(LIVE_URL, timeout=10)
        data = r.json()
        if data.get("result") == "success":
            php = data.get("rates", {}).get("PHP")
            if php:
                return round(float(php), 4)
    except Exception:
        pass
    return None


def get_rate(sheets):
    """
    Return the rate to use right now, refreshing from the live source once a day.
    `sheets` is the module that reads/writes the Settings tab.
    """
    today = config.today_iso()  # Manila date, not the server's UTC date

    stored_rate = sheets.get_setting("USD_TO_PHP_RATE")
    stored_date = sheets.get_setting("RATE_UPDATED")
    manual = sheets.get_setting("RATE_IS_MANUAL")  # "yes" if you used /setrate today

    # If you set it manually today, respect that and don't overwrite it.
    if manual == "yes" and stored_date == today and stored_rate:
        return float(stored_rate)

    # If we already refreshed the live rate today, reuse it.
    if stored_date == today and stored_rate:
        return float(stored_rate)

    # Otherwise refresh from the live source.
    live = fetch_live_rate()
    if live:
        sheets.set_setting("USD_TO_PHP_RATE", live)
        sheets.set_setting("RATE_UPDATED", today)
        sheets.set_setting("RATE_IS_MANUAL", "no")
        return live

    # Live source failed — use whatever is stored, else the .env fallback.
    if stored_rate:
        return float(stored_rate)
    return config.DEFAULT_USD_TO_PHP_RATE


def set_manual_rate(sheets, rate):
    """Save a manual rate from /setrate."""
    today = config.today_iso()
    sheets.set_setting("USD_TO_PHP_RATE", round(float(rate), 4))
    sheets.set_setting("RATE_UPDATED", today)
    sheets.set_setting("RATE_IS_MANUAL", "yes")
