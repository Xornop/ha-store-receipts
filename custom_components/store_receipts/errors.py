"""Errors shared by all stores."""


class ReceiptApiError(Exception):
    """Generic API or connection error."""


class ReceiptAuthError(ReceiptApiError):
    """The login (code or token) is invalid or expired."""
