"""Provider error taxonomy.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for anything that went wrong talking to an external service."""

    kind: str = "provider_error"
    retryable: bool = False

    def __init__(self, message: str, *, kind: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        if retryable is not None:
            self.retryable = retryable


class RateLimitError(ProviderError):
    kind = "rate_limit"
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class TransientProviderError(ProviderError):
    """5xx, connection reset, timeout — worth another attempt."""

    kind = "transient"
    retryable = True


class TerminalProviderError(ProviderError):
    """Bad request, auth failure, content filter — retrying cannot help."""

    kind = "terminal"
    retryable = False
