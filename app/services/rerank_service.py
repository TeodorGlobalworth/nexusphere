from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask import current_app

from .ai_providers import (
    NovitaAIProvider,
    RankedDocument,
    RerankCandidate,
    TokenUsage,
    ZeroEntropyProvider,
)


class RerankService:
    """Orchestrates reranking across supported providers with graceful fallbacks."""

    def __init__(self) -> None:
        self._zeroentropy: Optional[ZeroEntropyProvider] = None
        self._novita: Optional[NovitaAIProvider] = None

    @property
    def logger(self) -> logging.Logger:
        try:
            return current_app.logger
        except Exception:
            return logging.getLogger(__name__)

    def _get_provider(self, name: str):
        normalized = (name or "none").strip().lower()
        if normalized in ("none", "", "off", "disabled"):
            return None
        if normalized in {"zeroentropy", "ze"}:
            if self._zeroentropy is None:
                self._zeroentropy = ZeroEntropyProvider(logger=self.logger)
            return self._zeroentropy
        if normalized in {"novita", "novitaai", "novita_ai"}:
            if self._novita is None:
                self._novita = NovitaAIProvider()
            return self._novita
        return None

    def _to_candidates(self, documents: Sequence[Dict[str, Any]]) -> List[RerankCandidate]:
        candidates: List[RerankCandidate] = []
        for doc in documents:
            if isinstance(doc, RerankCandidate):
                candidates.append(doc)
                continue
            if not isinstance(doc, dict):
                continue
            metadata = doc.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            candidates.append(
                RerankCandidate(
                    id=doc.get("id"),
                    text=doc.get("content") or doc.get("text") or "",
                    metadata=metadata,
                    score=doc.get("score"),
                )
            )
        return candidates

    def _convert_ranked(
        self,
        ranked: Sequence[RankedDocument],
        originals: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        original_lookup: Dict[str, Dict[str, any]] = {}
        for doc in originals:
            key = str(doc.get("id")) if doc.get("id") is not None else None
            if key is not None:
                original_lookup[key] = doc
        converted: List[Dict[str, any]] = []
        for idx, ranked_doc in enumerate(ranked):
            key = str(ranked_doc.id) if ranked_doc.id is not None else None
            base: Optional[Dict[str, Any]] = original_lookup.get(key) if key else None

            if base is None and originals:
                meta = ranked_doc.metadata if isinstance(ranked_doc.metadata, dict) else {}
                index_hint = None
                if meta and "__novita_index" in meta:
                    try:
                        index_hint = int(meta.get("__novita_index"))
                    except (TypeError, ValueError):
                        index_hint = None
                if index_hint is not None and 0 <= index_hint < len(originals):
                    base = originals[index_hint]

            if base is None and idx < len(originals):
                base = originals[idx]

            if base is None and originals:
                probe = (ranked_doc.text or "").strip()
                if probe:
                    prefix = probe[:120]
                    base = next(
                        (
                            item
                            for item in originals
                            if (item.get("content") or "").startswith(prefix)
                        ),
                        None,
                    )
            if base is not None:
                metadata = base.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
            else:
                metadata = {}
                base = {"id": ranked_doc.id, "content": ranked_doc.text, "score": ranked_doc.score, "metadata": metadata}
            metadata = dict(metadata)
            metadata.pop("__novita_index", None)
            metadata.update(
                {
                    "rerank_score": ranked_doc.score,
                    "rerank_rank": ranked_doc.rank,
                    "rerank_provider": ranked_doc.provider,
                }
            )
            converted.append(
                {
                    **base,
                    "score": ranked_doc.score,
                    "metadata": metadata,
                }
            )
        return converted

    def rerank_documents(
        self,
        *,
        query: str,
        documents: Sequence[Dict[str, Any]],
        provider: Optional[str] = None,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], TokenUsage]:
        if not documents:
            return [], TokenUsage()

        provider_name = provider or current_app.config.get("RERANK_PROVIDER", "none")
        top_limit = top_k if top_k is not None else current_app.config.get("RERANK_TOP_K_DEFAULT", len(documents))
        score_threshold = threshold
        if score_threshold is None:
            score_threshold = current_app.config.get("RERANK_THRESHOLD_DEFAULT")

        provider_client = self._get_provider(provider_name)
        if provider_client is None:
            # Fallback: apply score threshold on existing vector scores only
            filtered = [doc for doc in documents if score_threshold is None or (doc.get("score") or 0) >= score_threshold]
            filtered.sort(key=lambda item: item.get("score") or 0, reverse=True)
            return filtered[:top_limit], TokenUsage()

        def _capture_diagnostics(label: str) -> Dict[str, Any]:
            if not hasattr(provider_client, "last_details"):
                return {"status": "unavailable", "label": label}
            try:
                snapshot = dict(getattr(provider_client, "last_details") or {})
                snapshot.setdefault("label", label)
                return snapshot
            except Exception as err:
                return {"status": "diagnostics_error", "label": label, "error": str(err)}

        provider_diagnostics: Dict[str, Any] = _capture_diagnostics("pre_call")

        candidates = self._to_candidates(documents)
        usage = TokenUsage()
        try:
            ranked, usage = provider_client.rerank(
                query=query,
                candidates=candidates,
                top_k=top_limit,
                threshold=score_threshold,
                model_override=model,
            )
            provider_diagnostics = _capture_diagnostics("post_call")
        except Exception as exc:
            provider_diagnostics = _capture_diagnostics("error")
            error_type = type(exc).__name__
            error_message = str(exc) or repr(exc)
            log_message = (
                "Rerank provider failed; falling back to raw vector ordering"
                f" [{error_type}] {error_message} | diagnostics={provider_diagnostics}"
            )
            self.logger.error(
                log_message,
                extra={
                    "event": "rerank_provider_error",
                    "provider": provider_name,
                    "error": str(exc),
                    "error_type": error_type,
                    "provider_diagnostics": provider_diagnostics,
                },
                exc_info=True,
            )
            filtered = [doc for doc in documents if score_threshold is None or (doc.get("score") or 0) >= score_threshold]
            filtered.sort(key=lambda item: item.get("score") or 0, reverse=True)
            return filtered[:top_limit], TokenUsage()

        if not ranked:
            provider_diagnostics = _capture_diagnostics("empty_result")
            self.logger.warning(
                "Rerank provider returned no documents after filtering | diagnostics=%s",
                provider_diagnostics,
                extra={
                    "event": "rerank_provider_no_results",
                    "provider": provider_name,
                    "top_limit": top_limit,
                    "threshold": score_threshold,
                    "candidate_count": len(candidates),
                    "provider_diagnostics": provider_diagnostics,
                },
            )
            filtered = [doc for doc in documents if score_threshold is None or (doc.get("score") or 0) >= score_threshold]
            filtered.sort(key=lambda item: item.get("score") or 0, reverse=True)
            return filtered[:top_limit], usage

        converted = self._convert_ranked(ranked, documents)
        if score_threshold is not None:
            converted = [doc for doc in converted if (doc.get("score") or 0) >= score_threshold]
        if top_limit is not None:
            converted = converted[:top_limit]
        return converted, usage
