from __future__ import annotations

from typing import Dict, Optional

from app.services.ai_providers.types import TokenUsage


class UsageTracker:
    """Collects token usage across pipeline steps and provides snapshots."""

    def __init__(self) -> None:
        self._buckets: Dict[str, TokenUsage] = {}

    def reset(self) -> None:
        self._buckets = {}

    def merge(self, other: 'UsageTracker') -> None:
        for channel, usage in other._buckets.items():
            self.track(channel, usage)

    def track(self, channel: str, usage: Optional[TokenUsage]) -> None:
        if not usage:
            return
        bucket = self._buckets.get(channel)
        if bucket is None:
            bucket = TokenUsage()
            self._buckets[channel] = bucket
        bucket.add(usage)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        return {
            name: usage.to_dict()
            for name, usage in self._buckets.items()
        }

    def get(self, channel: str) -> Optional[TokenUsage]:
        return self._buckets.get(channel)
