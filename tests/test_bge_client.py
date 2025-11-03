from __future__ import annotations

from typing import List

import pytest
from unittest.mock import Mock

from app.services.bge_client import (
    BGEClient,
    BGEClientError,
    BGEResult,
    BGESparseVector,
    _RetryableBGEError,
)


def _make_result() -> BGEResult:
    sparse = BGESparseVector(indices=[1, 3], values=[0.5, 0.7])
    return BGEResult(
        lexical=[sparse],
        colbert_tokens=[[[0.1, 0.2], [0.3, 0.4]]],
        colbert_agg=[[0.9, 0.8]],
        meta={"model": "bge-m3"},
    )


def test_encode_success_parses_response(monkeypatch):
    mock_session = Mock()
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    mock_response.json.return_value = {
        "lexical_sparse": [{"indices": [1, 3], "values": [0.5, 0.7]}],
        "colbert": [[[0.1, 0.2], [0.3, 0.4]]],
        "colbert_agg": [[0.9, 0.8]],
        "meta": {"model": "bge-m3"},
    }
    mock_session.post.return_value = mock_response

    client = BGEClient(base_url="http://bge", session=mock_session)
    result = client.encode([" hello "], return_dense=False)

    assert isinstance(result, BGEResult)
    assert result.first_sparse() == BGESparseVector(indices=[1, 3], values=[0.5, 0.7])
    assert result.first_colbert() == [[0.1, 0.2], [0.3, 0.4]]
    assert result.first_colbert_agg() == [0.9, 0.8]
    assert result.meta["model"] == "bge-m3"


def test_encode_retries_then_succeeds(monkeypatch):
    client = BGEClient(base_url="http://bge")

    attempt_log: List[int] = []

    def fake_request(self, payload):
        attempt_log.append(1)
        if len(attempt_log) == 1:
            raise _RetryableBGEError("temporary")
        return _make_result()

    mock_logger = Mock()
    sleep_calls: List[float] = []

    monkeypatch.setattr(BGEClient, "_perform_encode_request", fake_request, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_attempts", lambda self: 3, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_backoff", lambda self: 0.2, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_jitter", lambda self: 0.0, raising=False)
    monkeypatch.setattr("app.services.bge_client.random.uniform", lambda a, b: 0.0)
    monkeypatch.setattr("app.services.bge_client.time.sleep", lambda value: sleep_calls.append(value))
    monkeypatch.setattr("app.services.bge_client._logger", lambda: mock_logger)

    result = client.encode(["query"])

    assert isinstance(result, BGEResult)
    assert len(attempt_log) == 2
    assert sleep_calls == [0.2]
    mock_logger.warning.assert_called_once()


def test_encode_retries_exhausted(monkeypatch):
    client = BGEClient(base_url="http://bge")

    def always_fail(self, payload):  # pragma: no cover - patched behaviour
        raise _RetryableBGEError("busy")

    sleep_calls: List[float] = []

    monkeypatch.setattr(BGEClient, "_perform_encode_request", always_fail, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_attempts", lambda self: 2, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_backoff", lambda self: 0.1, raising=False)
    monkeypatch.setattr(BGEClient, "_retry_jitter", lambda self: 0.0, raising=False)
    monkeypatch.setattr("app.services.bge_client.time.sleep", lambda value: sleep_calls.append(value))

    with pytest.raises(BGEClientError) as excinfo:
        client.encode(["query"])

    assert "busy" in str(excinfo.value)
    assert sleep_calls == [0.1]


def test_encode_rejects_empty_sentences():
    client = BGEClient(base_url="http://bge")

    with pytest.raises(ValueError):
        client.encode([" ", ""])