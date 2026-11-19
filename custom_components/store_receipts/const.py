"""Constants for the Store Receipts integration."""
from datetime import timedelta

DOMAIN = "store_receipts"
PLATFORMS = ["sensor"]

CONF_PROVIDER = "provider"
CONF_CODE = "code"
CONF_REFRESH_TOKEN = "refresh_token"

# Token fields kept in the config entry
TOKEN_KEYS = ("access_token", "refresh_token", "expires_at")

SCAN_INTERVAL = timedelta(hours=1)
