"""Expose :class:`Config` at package level for import strings ("config.Config")."""

from .config import Config, TestingConfig  # noqa: F401

__all__ = ["Config", "TestingConfig"]
