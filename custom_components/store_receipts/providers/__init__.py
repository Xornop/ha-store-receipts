"""All supported stores.

To add a store: create providers/<store>/ (api.py, optional parser.py and an
__init__.py that defines PROVIDER), then list it below.
"""
from .action import PROVIDER as ACTION
from .ah import PROVIDER as AH
from .base import Provider
from .jumbo import PROVIDER as JUMBO
from .lidl import PROVIDER as LIDL

PROVIDERS: dict[str, Provider] = {
    provider.key: provider for provider in (AH, JUMBO, LIDL, ACTION)
}
