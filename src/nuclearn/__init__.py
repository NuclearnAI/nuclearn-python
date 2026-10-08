"""Nuclearn platform client library."""

from .client import Client, UploadResult, auth_check, connection_check
from .errors import (
    AuthenticationError,
    ConfigurationError,
    NuclearnError,
    UploadError,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "UploadResult",
    "connection_check",
    "auth_check",
    "NuclearnError",
    "ConfigurationError",
    "AuthenticationError",
    "UploadError",
    "__version__",
]
