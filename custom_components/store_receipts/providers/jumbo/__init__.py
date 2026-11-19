"""Jumbo."""
from ..base import Provider
from .api import JumboApi, async_refresh_tokens

PROVIDER = Provider(
    key="jumbo",
    name="Jumbo",
    api_class=JumboApi,
    refresh_tokens=async_refresh_tokens,
)
