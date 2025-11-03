from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests
from flask import current_app


def _logger() -> logging.Logger:
    try:
        return current_app.logger
    except Exception:  # pragma: no cover - fallback outside app context
        return logging.getLogger(__name__)


class BGEClientError(RuntimeError):
    """Raised when the BGE service cannot complete a request."""


class _RetryableBGEError(BGEClientError):
    """Internal helper to mark retryable failures."""


@dataclass(slots=True)
class BGESparseVector:
    indices: List[int]
    values: List[float]


@dataclass(slots=True)
class BGEResult:
    lexical: List[BGESparseVector]
    colbert_tokens: List[List[List[float]]]
    colbert_agg: List[List[float]]
    meta: Dict[str, object]

    def first_sparse(self) -> Optional[BGESparseVector]:
        return self.lexical[0] if self.lexical else None

    def first_colbert(self) -> Optional[List[List[float]]]:
        return self.colbert_tokens[0] if self.colbert_tokens else None

    def first_colbert_agg(self) -> Optional[List[float]]:
        return self.colbert_agg[0] if self.colbert_agg else None


class BGEClient:
    """Thin HTTP client for the local BGE-M3 embedding service."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._explicit_base_url = base_url
        self._explicit_timeout = timeout
        self._session = session or requests.Session()

    def _base_url(self) -> str:
        base_url = self._explicit_base_url
        if not base_url:
            try:
                base_url = current_app.config.get("BGE_M3_BASE_URL")
            except Exception as exc:
                raise BGEClientError("BGE_M3_BASE_URL is not configured") from exc
        if not base_url:
            raise BGEClientError("BGE_M3_BASE_URL is empty")
        return base_url.rstrip("/")

    def _timeout(self) -> float:
        if self._explicit_timeout is not None:
            return float(self._explicit_timeout)
        try:
            return float(current_app.config.get("BGE_M3_TIMEOUT", 30.0))
        except Exception:
            return 30.0

    def _retry_attempts(self) -> int:
        try:
            return max(1, int(current_app.config.get("BGE_RETRY_ATTEMPTS", 3)))
        except Exception:
            return 3

    def _retry_backoff(self) -> float:
        try:
            value = float(current_app.config.get("BGE_RETRY_BACKOFF_SECS", 1.0))
        except Exception:
            value = 1.0
        return max(0.0, value)

    def _retry_jitter(self) -> float:
        try:
            value = float(current_app.config.get("BGE_RETRY_JITTER_SECS", 0.5))
        except Exception:
            value = 0.5
        return max(0.0, value)

    def encode(
        self,
        sentences: Sequence[str],
        *,
        return_dense: bool = False,
        return_colbert_vecs: bool = True,
    ) -> BGEResult:
        sentences = [item for item in sentences if (item or "").strip()]
        if not sentences:
            raise ValueError("sentences must contain at least one non-empty item")

        payload = {
            "sentences": sentences,
            "return_dense": bool(return_dense),
            "return_sparse": True,
            "return_colbert_vecs": bool(return_colbert_vecs),
        }
        attempts = self._retry_attempts()
        base_delay = self._retry_backoff()
        jitter = self._retry_jitter()
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self._perform_encode_request(payload)
            except _RetryableBGEError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise BGEClientError(str(exc)) from exc
                sleep_for = base_delay * attempt
                if jitter > 0:
                    sleep_for += random.uniform(0, jitter)
                _logger().warning(
                    "Retrying BGE encode (attempt %s/%s): %s",
                    attempt,
                    attempts,
                    exc,
                    extra={'event': 'bge_retry', 'attempt': attempt, 'attempts': attempts},
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)
            except BGEClientError:
                raise

        raise BGEClientError(str(last_error) if last_error else "BGE request failed")

    def _perform_encode_request(self, payload: Dict[str, object]) -> BGEResult:
        url = f"{self._base_url()}/encode"
        try:
            response = self._session.post(url, json=payload, timeout=self._timeout())
        except requests.Timeout as exc:  # pragma: no cover - network boundary
            raise _RetryableBGEError("BGE service timed out") from exc
        except requests.ConnectionError as exc:  # pragma: no cover - network boundary
            raise _RetryableBGEError(f"BGE connection error: {exc}") from exc
        except requests.RequestException as exc:  # pragma: no cover - network boundary
            raise _RetryableBGEError(f"BGE request failed: {exc}") from exc

        status = response.status_code
        if status in (408, 429) or status >= 500:
            raise _RetryableBGEError(f"BGE service unavailable ({status}): {response.text}")
        if status >= 400:
            raise BGEClientError(f"BGE service error {status}: {response.text}")

        try:
            data = response.json()
        except ValueError as exc:
            raise _RetryableBGEError("Invalid JSON payload from BGE service") from exc

        lexical_raw = data.get("lexical_sparse") or []
        lexical: List[BGESparseVector] = []
        for row in lexical_raw:
            if not isinstance(row, dict):
                continue
            indices = row.get("indices") or []
            values = row.get("values") or []
            if not isinstance(indices, list) or not isinstance(values, list):
                continue
            try:
                indices_list = [int(i) for i in indices]
                values_list = [float(v) for v in values]
            except (TypeError, ValueError):
                continue
            lexical.append(BGESparseVector(indices=indices_list, values=values_list))

        colbert_tokens = data.get("colbert") or []
        if not isinstance(colbert_tokens, list):
            colbert_tokens = []

        colbert_agg = data.get("colbert_agg") or []
        if not isinstance(colbert_agg, list):
            colbert_agg = []

        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        return BGEResult(
            lexical=lexical,
            colbert_tokens=colbert_tokens,
            colbert_agg=colbert_agg,
            meta=meta,
        )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover - guard
            pass

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        self.close()