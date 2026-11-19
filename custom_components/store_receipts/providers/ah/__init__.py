"""Albert Heijn."""
from ..base import Provider
from .api import AHApi, async_exchange_code, async_refresh_tokens, extract_code

PROVIDER = Provider(
    key="ah",
    name="Albert Heijn",
    api_class=AHApi,
    refresh_tokens=async_refresh_tokens,
    exchange_code=async_exchange_code,
    extract_code=extract_code,
)
