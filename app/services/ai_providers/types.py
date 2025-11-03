from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TokenUsage:
    """Normalized token accounting used across AI providers."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def add(self, other: "TokenUsage") -> "TokenUsage":
        if not other:
            return self
        self.prompt_tokens += other.prompt_tokens or 0
        self.completion_tokens += other.completion_tokens or 0
        return self

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TokenUsage":
        if not data:
            return cls()
        return cls(
            prompt_tokens=int(data.get("prompt_tokens") or data.get("input_tokens") or 0),
            completion_tokens=int(data.get("completion_tokens") or data.get("output_tokens") or 0),
        )


@dataclass
class RerankCandidate:
    """Candidate document to be re-ranked."""

    id: Any
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None


@dataclass
class RankedDocument:
    """Reranked document including final score and original payload."""

    id: Any
    text: str
    score: float
    rank: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_score: Optional[float] = None
    provider: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "text": self.text,
            "score": self.score,
            "rank": self.rank,
            "metadata": self.metadata,
        }
        if self.source_score is not None:
            payload["source_score"] = self.source_score
        if self.provider:
            payload["provider"] = self.provider
        return payload
