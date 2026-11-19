"""Action."""
from ..base import Provider
from .api import ActionApi, async_refresh_tokens

PROVIDER = Provider(
    key="action",
    name="Action",
    api_class=ActionApi,
    refresh_tokens=async_refresh_tokens,
)
