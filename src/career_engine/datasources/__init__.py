"""Pluggable data sources behind the :class:`DataSource` protocol."""

from __future__ import annotations

from .base import DataSource
from .registry import build_sources

__all__ = ["DataSource", "build_sources"]
