import io
import struct
import socket

import pytest

from app.utils.clamav_client import (
    ClamAVClient,
    ClamAVConnectionError,
)


class FakeSocket:
    """Minimal socket stub capturing writes and returning queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._writes = []

    @property
    def writes(self):
        return self._writes

    def sendall(self, data):
        self._writes.append(data)

    def recv(self, bufsize):  # pragma: no cover - behaviour dictated by queued responses
        if not self._responses:
            return b""
        return self._responses.pop(0)

    # Context manager API used by ClamAVClient
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_ping_success(monkeypatch):
    fake_sock = FakeSocket([b"PONG\n"])

    def fake_create_conn(addr, timeout=None):
        return fake_sock

    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    client = ClamAVClient()
    assert client.ping() is True
    assert fake_sock.writes == [b"nPING\n"]


def test_instream_ok(monkeypatch):
    fake_sock = FakeSocket([b"stream: OK\x00\n"])

    def fake_create_conn(addr, timeout=None):
        return fake_sock

    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    payload = io.BytesIO(b"hello world")
    client = ClamAVClient()
    result = client.instream(payload, chunk_size=4)

    assert result == {"stream": ("OK", None)}
    # First write switches to INSTREAM mode
    assert fake_sock.writes[0] == b"zINSTREAM\0"
    # Last write terminates the stream (zero-length chunk)
    assert fake_sock.writes[-1] == struct.pack("!I", 0)


def test_instream_detects_malware(monkeypatch):
    fake_sock = FakeSocket([b"stream: Eicar-Test-Signature FOUND\n"])

    def fake_create_conn(addr, timeout=None):
        return fake_sock

    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    payload = io.BytesIO(b"dummy")
    client = ClamAVClient()
    result = client.instream(payload)

    assert result == {"stream": ("FOUND", "Eicar-Test-Signature")}


def test_connect_retries_and_raises(monkeypatch):
    attempts = []

    def fake_create_conn(addr, timeout=None):
        attempts.append(addr)
        raise OSError("boom")

    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    client = ClamAVClient(retries=3, retry_delay=0)
    with pytest.raises(ClamAVConnectionError):
        client.ping()

    assert len(attempts) == 3
