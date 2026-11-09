"""Unofficial client for the Plaud 4.0 web API: upload recordings, keep the session alive."""
__version__ = "0.1.0"

from .client import Plaud  # noqa: E402
from .http import PlaudError  # noqa: E402

__all__ = ["Plaud", "PlaudError", "__version__"]
