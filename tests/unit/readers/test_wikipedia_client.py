import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from tomd.errors import NetworkError, SourceNotFoundError, UnsupportedSourceError
from tomd.readers.wikipedia.client import (
    USER_AGENT,
    ArticleRef,
    WikimediaClient,
    parse_article_ref,
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeNetwork:
    """Replays queued responses (bytes or HTTP status codes) and records requests."""

    def __init__(self, *responses: bytes | tuple[int, str | None]) -> None:
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, tuple):
            status, retry_after = item
            headers = Message()
            if retry_after is not None:
                headers["Retry-After"] = retry_after
            raise urllib.error.HTTPError(request.full_url, status, "err", headers, None)
        return FakeResponse(item)


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(net: FakeNetwork, t: FakeTime) -> WikimediaClient:
    return WikimediaClient(opener=net, sleep=t.sleep, clock=t.clock)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://es.wikipedia.org/wiki/Teorema_de_Bayes", ArticleRef("es", "Teorema_de_Bayes")),
        (
            "https://en.m.wikipedia.org/wiki/Bayes%27_theorem#History",
            ArticleRef("en", "Bayes'_theorem"),
        ),
        ("es:Teorema de Bayes", ArticleRef("es", "Teorema_de_Bayes")),
    ],
)
def test_parse_article_ref(source: str, expected: ArticleRef) -> None:
    assert parse_article_ref(source) == expected


@pytest.mark.parametrize("source", ["https://example.org/wiki/X", "C:\\x.html", "notes.txt"])
def test_parse_article_ref_rejects_other_sources(source: str) -> None:
    with pytest.raises(UnsupportedSourceError):
        parse_article_ref(source)


def test_fetch_by_revision_with_user_agent_and_core_rest_paths() -> None:
    net = FakeNetwork(json.dumps({"latest": {"id": 42}}).encode(), b"<html/>")
    t = FakeTime()
    client = _client(net, t)
    ref = ArticleRef("es", "Teorema_de_Bayes")
    assert client.latest_revision(ref) == 42
    assert client.revision_html(ref, 42) == b"<html/>"
    assert [r.full_url for r in net.requests] == [
        "https://es.wikipedia.org/w/rest.php/v1/page/Teorema_de_Bayes/bare",
        "https://es.wikipedia.org/w/rest.php/v1/revision/42/html",
    ]
    assert all(r.get_header("User-agent") == USER_AGENT for r in net.requests)
    assert "github.com" in USER_AGENT


def test_requests_are_rate_limited() -> None:
    net = FakeNetwork(b"a", b"b")
    t = FakeTime()
    client = _client(net, t)
    ref = ArticleRef("en", "X")
    client.revision_html(ref, 1)
    client.revision_html(ref, 2)
    assert t.sleeps == [pytest.approx(1.0)]


def test_retry_after_is_honoured() -> None:
    net = FakeNetwork((429, "7"), b"ok")
    t = FakeTime()
    assert _client(net, t).revision_html(ArticleRef("en", "X"), 1) == b"ok"
    assert 7.0 in t.sleeps


def test_gives_up_after_max_retries() -> None:
    net = FakeNetwork((503, None), (503, None), (503, None), (503, None))
    with pytest.raises(NetworkError):
        _client(net, FakeTime()).revision_html(ArticleRef("en", "X"), 1)
    assert len(net.requests) == 4


def test_not_found_is_not_retried() -> None:
    net = FakeNetwork((404, None))
    with pytest.raises(SourceNotFoundError):
        _client(net, FakeTime()).revision_html(ArticleRef("en", "X"), 1)
    assert len(net.requests) == 1


def test_real_network_is_blocked_in_tests() -> None:
    with pytest.raises((RuntimeError, NetworkError)):
        WikimediaClient(max_retries=0).revision_html(ArticleRef("en", "X"), 1)
