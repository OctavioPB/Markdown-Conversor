"""Shared fixtures. Network access is blocked in every test (see docs/TESTING.md)."""

import socket
from typing import Any

import pytest


class NetworkAccessError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guard(*args: Any, **kwargs: Any) -> None:
        raise NetworkAccessError("Tests must not touch the network; use recorded fixtures.")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
