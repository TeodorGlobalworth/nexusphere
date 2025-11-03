from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence, Tuple

import requests
from flask import current_app

from .types import RankedDocument, RerankCandidate, TokenUsage


class NovitaAIProvider:
    """Client wrapper for NovitaAI rerank OpenAI-compatible endpoint."""

    BASE_URL = "https://api.novita.ai"
    ENDPOINT = "/openai/v1/rerank"

    def __init__(self, *, session: Optional[requests.Session] = None):
        self._session = session or requests.Session()

    @property
    def logger(self) -> logging.Logger:
        try:
            return current_app.logger
        except Exception:
            return logging.getLogger(__name__)

    def _api_key(self) -> str:
        api_key = current_app.config.get("NOVITA_API_KEY")
        if not api_key:
            raise RuntimeError("NOVITA_API_KEY is not configured")
        return api_key

    def _model(self) -> str:
        return current_app.config.get("NOVITA_RERANK_MODEL", "qwen/qwen3-reranker-8b")

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[List[RankedDocument], TokenUsage]:
        if not candidates:
            return [], TokenUsage()

        body = {
            "model": model_override or self._model(),
            "query": query,
            "documents": [candidate.text for candidate in candidates],
            "return_documents": True,
        }
        if top_k is not None:
            body["top_n"] = top_k

        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

        url = f"{self.BASE_URL.rstrip('/')}{self.ENDPOINT}"
        response = None
        try:
            response = self._session.post(url, json=body, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            response_text = ""
            if getattr(exc, "response", None) is not None:
                try:
                    response_text = exc.response.text[:500]
                except Exception:
                    response_text = ""
            self.logger.error(
                "NovitaAI rerank request failed",
                extra={
                    "event": "novita_rerank_error",
                    "status_code": status_code,
                    "error": str(exc),
                    "response": response_text,
                },
            )
            raise

        payload = response.json() if response is not None and response.content else {}
        usage = TokenUsage.from_dict((payload or {}).get("usage"))

        items: Iterable[dict] = (
            (payload or {}).get("data")
            or (payload or {}).get("results")
            or []
        )

        ranked: List[RankedDocument] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            doc_index = item.get("index")
            if doc_index is None:
                doc_index = item.get("document_index")
            candidate = None
            if isinstance(doc_index, (int, float, str)):
                try:
                    doc_index_int = int(doc_index)
                except (TypeError, ValueError):
                    doc_index_int = None
            else:
                doc_index_int = None

            if doc_index_int is not None and 0 <= doc_index_int < len(candidates):
                candidate = candidates[doc_index_int]
            if candidate is None and item.get("document"):
                doc_payload = item["document"]
                candidate = RerankCandidate(
                    id=doc_payload.get("id"),
                    text=doc_payload.get("text", ""),
                    metadata=doc_payload.get("metadata") or {},
                )
            if candidate is None:
                continue

            score = item.get("relevance_score")
            if score is None:
                score = item.get("score")
            if score is None:
                score = item.get("similarity")
            if score is None:
                try:
                    score = float(item.get("value"))
                except Exception:
                    score = None
            if score is None:
                continue

            if threshold is not None and score < threshold:
                continue

            metadata_copy = dict(candidate.metadata or {})
            if doc_index_int is not None:
                metadata_copy.setdefault("__novita_index", doc_index_int)
            ranked.append(
                RankedDocument(
                    id=candidate.id,
                    text=candidate.text,
                    score=float(score),
                    rank=int(item.get("position", len(ranked))),
                    metadata=metadata_copy,
                    source_score=candidate.score,
                    provider="novita",
                )
            )

        ranked.sort(key=lambda doc: doc.score, reverse=True)
        if top_k is not None:
            ranked = ranked[:top_k]

        for pos, doc in enumerate(ranked):
            doc.rank = pos

        return ranked, usage
