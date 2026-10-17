"""Minimal Wikimedia core REST API client (ADR-013).

Sequential requests, at most one per `min_interval` seconds, identifiable User-Agent,
bounded retries honouring `Retry-After`. Network access happens only here.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from tomd import __version__
from tomd.errors import NetworkError, SourceNotFoundError, UnsupportedSourceError

PROJECT_URL = "https://github.com/OctavioPB/Markdown-Conversor"
USER_AGENT = f"tomd/{__version__} ({PROJECT_URL})"

# Wikimedia asks for sequential, rate-limited clients; one request per second is well
# below the documented limits and keeps a batch of articles polite.
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_RETRIES = 3
# Never wait longer than this on a single Retry-After; beyond it the request fails.
MAX_RETRY_AFTER = 60.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_WIKI_URL = re.compile(r"^https?://([a-z][a-z-]{1,15})\.(?:m\.)?wikipedia\.org/wiki/([^?#]+)")
_LANG_TITLE = re.compile(r"^([a-z][a-z-]{1,15}):(.+)$")


@dataclass(frozen=True, slots=True)
class ArticleRef:
    lang: str
    title: str  # with underscores, not percent-encoded

    @property
    def host(self) -> str:
        return f"{self.lang}.wikipedia.org"

    @property
    def url(self) -> str:
        return f"https://{self.host}/wiki/{urllib.parse.quote(self.title, safe='')}"


def parse_article_ref(source: str) -> ArticleRef:
    """Accept `https://es.wikipedia.org/wiki/Título` or `es:Título`."""
    m = _WIKI_URL.match(source.strip())
    if m:
        title = urllib.parse.unquote(m.group(2))
        return ArticleRef(m.group(1), title.replace(" ", "_"))
    m = _LANG_TITLE.match(source.strip())
    if m and not m.group(2).startswith("/"):  # "https://other.site/..." is not lang:Title
        return ArticleRef(m.group(1), m.group(2).strip().replace(" ", "_"))
    raise UnsupportedSourceError(f"Not a Wikipedia article reference: {source!r}")


class _Response(Protocol):
    def read(self) -> bytes: ...
    def __enter__(self) -> "_Response": ...
    def __exit__(self, *args: object) -> None: ...


type Opener = Callable[[urllib.request.Request, float], _Response]


def _default_opener(request: urllib.request.Request, timeout: float) -> _Response:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[no-any-return]


@dataclass
class WikimediaClient:
    user_agent: str = USER_AGENT
    min_interval: float = DEFAULT_MIN_INTERVAL
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout: float = 30.0
    opener: Opener = _default_opener
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _last_request: float | None = field(default=None, repr=False)

    def latest_revision(self, ref: ArticleRef) -> int:
        data = self._get_json(ref.host, f"/w/rest.php/v1/page/{_path(ref.title)}/bare")
        try:
            return int(data["latest"]["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NetworkError(f"Unexpected response for {ref.url}: no latest revision") from exc

    def revision_html(self, ref: ArticleRef, revision_id: int) -> bytes:
        return self._get(ref.host, f"/w/rest.php/v1/revision/{revision_id}/html")

    def _get_json(self, host: str, path: str) -> Any:
        try:
            return json.loads(self._get(host, path))
        except json.JSONDecodeError as exc:
            raise NetworkError(f"Invalid JSON from https://{host}{path}") from exc

    def _get(self, host: str, path: str) -> bytes:
        url = f"https://{host}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                with self.opener(request, self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise SourceNotFoundError(
                        f"Not found: {url}. Check the article title (and --revision, if given)."
                    ) from exc
                if exc.code not in RETRYABLE_STATUS or attempt == self.max_retries:
                    raise NetworkError(
                        f"HTTP {exc.code} for {url} after retries; Wikimedia may be busy, "
                        "try again later."
                    ) from exc
                self.sleep(_retry_delay(exc.headers.get("Retry-After"), attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.max_retries:
                    raise NetworkError(
                        f"Request failed for {url}: {exc}. Check the network connection."
                    ) from exc
                self.sleep(_retry_delay(None, attempt))
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        now = self.clock()
        if self._last_request is not None:
            wait = self.min_interval - (now - self._last_request)
            if wait > 0:
                self.sleep(wait)
                now += wait
        self._last_request = now


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_RETRY_AFTER)
        except ValueError:
            pass
    return min(2.0 ** (attempt + 1), MAX_RETRY_AFTER)


def _path(title: str) -> str:
    return urllib.parse.quote(title, safe="")
