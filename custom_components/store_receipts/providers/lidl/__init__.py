"""Lidl."""
from ..base import Provider
from .api import LidlApi, async_refresh_tokens

PROVIDER = Provider(
    key="lidl",
    name="Lidl",
    api_class=LidlApi,
    refresh_tokens=async_refresh_tokens,
)
