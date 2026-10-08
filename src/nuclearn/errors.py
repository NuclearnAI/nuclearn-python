"""Exceptions raised by the nuclearn client."""

from typing import Optional


class NuclearnError(Exception):
    """Base class for all nuclearn client errors."""


class ConfigurationError(NuclearnError):
    """The client is missing required configuration (URL or credentials)."""


class AuthenticationError(NuclearnError):
    """The platform rejected the provided credentials or API key."""


class UploadError(NuclearnError):
    """An upload request was rejected by the platform."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
