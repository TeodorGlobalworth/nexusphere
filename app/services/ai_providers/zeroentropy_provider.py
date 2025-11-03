from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask import current_app
from zeroentropy import ZeroEntropy, ZeroEntropyError
from zeroentropy._exceptions import APIStatusError

from .types import RankedDocument, RerankCandidate, TokenUsage


class ZeroEntropyProvider:
    """Thin adapter that mirrors the official SDK snippet."""

    DEFAULT_MODEL = "zerank-1"

    def __init__(self, *, client: Optional[ZeroEntropy] = None, logger: Optional[logging.Logger] = None) -> None:
        self._client = client
        self._logger = logger
        self._last_details: Dict[str, Any] = {}

    @property
    def last_details(self) -> Dict[str, Any]:
        return dict(self._last_details)

    @property
    def logger(self) -> logging.Logger:
        if self._logger is not None:
            return self._logger
        try:
            return current_app.logger
        except Exception:  # pragma: no cover - CLI/tests fallback
            return logging.getLogger(__name__)

    def _config(self) -> Dict[str, Any]:
        try:
            return current_app.config
        except Exception:  # pragma: no cover - CLI/tests fallback
            return {}

    def _api_key(self) -> str:
        api_key = self._config().get("ZEROENTROPY_API_KEY")
        if not api_key:
            raise RuntimeError("ZEROENTROPY_API_KEY is not configured")
        return str(api_key)

    def _model(self, override: Optional[str]) -> str:
        return str(override or self._config().get("ZEROENTROPY_MODEL") or self.DEFAULT_MODEL)

    def _client_instance(self) -> ZeroEntropy:
        if self._client is None:
            init_kwargs: Dict[str, Any] = {"api_key": self._api_key()}
            base_url = self._config().get("ZEROENTROPY_BASE_URL")
            if base_url:
                init_kwargs["base_url"] = str(base_url)
            self._client = ZeroEntropy(**init_kwargs)
        return self._client

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _usage(response: Any) -> TokenUsage:
        usage_obj = getattr(response, "usage", None)
        if not usage_obj:
            return TokenUsage()
        if isinstance(usage_obj, dict):
            return TokenUsage.from_dict(usage_obj)
        if hasattr(usage_obj, "model_dump"):
            try:
                return TokenUsage.from_dict(usage_obj.model_dump())
            except Exception:  # pragma: no cover - defensive
                return TokenUsage()
        if hasattr(usage_obj, "__dict__"):
            return TokenUsage.from_dict({k: v for k, v in usage_obj.__dict__.items() if not k.startswith("_")})
        return TokenUsage()

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
            self._last_details = {"status": "no_candidates", "provider": "zeroentropy"}
            return [], TokenUsage()

        model_name = self._model(model_override)
        documents = [self._text(candidate.text) for candidate in candidates]
        payload = {
            "model": model_name,
            "query": query,
            "documents": documents,
        }
        if top_k is not None:
            payload["top_n"] = int(top_k)

        self._last_details = {
            "status": "pending",
            "provider": "zeroentropy",
            "model": model_name,
            "candidate_count": len(documents),
            "top_k": top_k,
            "threshold": threshold,
        }

        self.logger.info(
            "ZeroEntropy rerank request",
            extra={"event": "zeroentropy_rerank_request", **self._last_details},
        )

        try:
            response = self._client_instance().models.rerank(**payload)
        except APIStatusError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            body_preview = ""
            if getattr(exc, "response", None) is not None:
                try:
                    body_preview = exc.response.text[:500]
                except Exception:  # pragma: no cover - defensive
                    body_preview = ""
            self._last_details.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "status_code": status_code,
                    "error": str(exc),
                    "response_preview": body_preview,
                },
            )
            self.logger.error(
                "ZeroEntropy HTTP error (status=%s)",
                status_code,
                extra={"event": "zeroentropy_rerank_error", **self._last_details},
                exc_info=True,
            )
            raise RuntimeError(f"ZeroEntropy HTTP {status_code}: {body_preview}"[:500]) from exc
        except ZeroEntropyError as exc:
            self._last_details.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
            self.logger.error(
                "ZeroEntropy client error",
                extra={"event": "zeroentropy_rerank_error", **self._last_details},
                exc_info=True,
            )
            raise RuntimeError(f"ZeroEntropy client error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._last_details.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc) or repr(exc)})
            self.logger.error(
                "ZeroEntropy unexpected error",
                extra={"event": "zeroentropy_rerank_error", **self._last_details},
                exc_info=True,
            )
            raise RuntimeError(f"ZeroEntropy unexpected error: {exc}") from exc

        results = list(getattr(response, "results", []) or [])
        ranked: List[RankedDocument] = []
        for item in results:
            index = getattr(item, "index", None)
            try:
                index_int = int(index) if index is not None else None
            except (TypeError, ValueError):
                index_int = None
            if index_int is None or not (0 <= index_int < len(candidates)):
                continue

            score = getattr(item, "relevance_score", None)
            if score is None:
                continue

            if threshold is not None and float(score) < float(threshold):
                continue

            source = candidates[index_int]
            ranked.append(
                RankedDocument(
                    id=source.id,
                    text=self._text(source.text),
                    score=float(score),
                    rank=len(ranked),
                    metadata=dict(source.metadata or {}),
                    source_score=source.score,
                    provider="zeroentropy",
                )
            )

        if top_k is not None:
            ranked = ranked[: top_k]

        for position, doc in enumerate(ranked):
            doc.rank = position

        usage = self._usage(response)
        self._last_details.update({"status": "success" if ranked else "empty", "returned_count": len(ranked)})

        self.logger.info(
            "ZeroEntropy rerank completed",
            extra={
                "event": "zeroentropy_rerank_completed",
                "returned": len(ranked),
                "top_score": ranked[0].score if ranked else None,
            },
        )

        return ranked, usage

