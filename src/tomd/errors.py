"""Typed errors. Inputs that cannot be converted at all raise; partial losses are
reported as Diagnostics instead."""


class TomdError(Exception):
    """Base class for every error tomd raises on purpose."""


class UnsupportedSourceError(TomdError):
    """No reader recognises the input."""


class UnreadableInputError(TomdError):
    """The input is corrupt, truncated or not what its extension claims."""


class DrmProtectedError(TomdError):
    """The input is DRM-protected; tomd does not handle DRM (ADR-009)."""


class SourceNotFoundError(TomdError):
    """The remote resource (e.g. a Wikipedia article or revision) does not exist."""


class NetworkError(TomdError):
    """A remote request failed after the allowed retries."""
