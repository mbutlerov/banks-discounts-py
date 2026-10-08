from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Callable, Iterator
from urllib.parse import urljoin, urlparse

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, Timeout
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
ALLOWED_HOSTS = ("ueno.com.py", "sudameris.com.py", "itau.com.py", "bancoatlas.com.py", "bancognb.com.py", "beneficiosbancognb.com.py")


class HttpClientError(Exception):
    pass


class HttpClientTimeoutError(HttpClientError):
    pass


class HttpClientRequestError(HttpClientError):
    pass


@dataclass(slots=True)
class HttpResponseData:
    url: str
    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


Observer = Callable[[str, HttpResponseData | None, str | None], None]
_observer: ContextVar[Observer | None] = ContextVar("http_observer", default=None)
_replay: ContextVar[dict[str, HttpResponseData] | None] = ContextVar("http_replay", default=None)
_cache: ContextVar[dict[str, HttpResponseData] | None] = ContextVar("http_cache", default=None)


@contextmanager
def http_scope(observer: Observer | None = None, replay: dict[str, HttpResponseData] | None = None) -> Iterator[None]:
    observation_token = _observer.set(observer)
    replay_token = _replay.set(replay)
    cache_token = _cache.set({})
    try:
        yield
    finally:
        _observer.reset(observation_token)
        _replay.reset(replay_token)
        _cache.reset(cache_token)


class HttpClient:
    """Bounded bank-public HTTP with snapshot hooks and network-free replay."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, default_headers: dict[str, str] | None = None, session: Session | None = None, *, httpx_fallback_hosts: tuple[str, ...] = (), httpx_client: httpx.Client | None = None, httpx_default_user_agent_on_fallback: bool = False) -> None:
        self._timeout = timeout
        self._session = session or requests.Session()
        if session is None:
            retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",), respect_retry_after_header=True, backoff_max=5, retry_after_max=30)
            self._session.mount("https://", HTTPAdapter(max_retries=retry))
            self._session.mount("http://", HTTPAdapter(max_retries=retry))
        self._default_headers = default_headers or {"User-Agent": "BanksDiscounts/1.0 (+public-benefit-catalog)"}
        self._httpx_fallback_hosts = frozenset(host.lower() for host in httpx_fallback_hosts)
        self._httpx_default_user_agent_on_fallback = httpx_default_user_agent_on_fallback
        # Replay/cache must remain independent of HTTPX and never create a client.
        self._httpx_client = httpx_client
        self._httpx_errors: tuple[type[Exception], ...] = ()
        self._httpx_timeouts: tuple[type[Exception], ...] = ()

    def close(self) -> None:
        self._session.close()
        if self._httpx_client is not None:
            self._httpx_client.close()

    def _request(self, url: str, headers: dict[str, str], timeout: int) -> Response | httpx.Response:
        response = self._session.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True)
        if response.status_code != 403 or (urlparse(url).hostname or "").lower() not in self._httpx_fallback_hosts:
            return response
        response.close()
        import httpx

        self._httpx_errors = (httpx.HTTPError,)
        self._httpx_timeouts = (httpx.TimeoutException,)
        logger.info("HTTP 403; retrying the authorized public bank URL with HTTPX: %s", url)
        if self._httpx_client is None:
            self._httpx_client = httpx.Client(follow_redirects=False)
        fallback_headers = ({key: value for key, value in headers.items() if key.lower() != "user-agent"}
                            if self._httpx_default_user_agent_on_fallback else headers)
        request = self._httpx_client.build_request("GET", url, headers=fallback_headers, timeout=timeout)
        return self._httpx_client.send(request, stream=True, follow_redirects=False)

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port not in (None, 80, 443) or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            raise HttpClientRequestError("URL fuera de las fuentes bancarias permitidas.")

    def get(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int | None = None, allow_redirects: bool = True) -> HttpResponseData:
        requested_url = requests.Request("GET", url, params=params).prepare().url or url
        observer = _observer.get()
        try:
            self._validate_url(requested_url)
            replay = _replay.get()
            cache = _cache.get()
            if replay is not None:
                if requested_url not in replay:
                    raise HttpClientRequestError("Documento ausente del replay; no se permite acceso a red.")
                data = replay[requested_url]
            elif cache is not None and requested_url in cache:
                return cache[requested_url]
            else:
                current_url = requested_url
                for _ in range(6):
                    self._validate_url(current_url)
                    response = self._request(current_url, {**self._default_headers, **(headers or {})}, timeout or self._timeout)
                    try:
                        if allow_redirects and response.is_redirect and "Location" in response.headers:
                            current_url = urljoin(current_url, response.headers["Location"])
                            continue
                        # requests permits an unfollowed 3xx response; HTTPX's
                        # raise_for_status also rejects 3xx, so check consistently.
                        if response.status_code >= 400:
                            response.raise_for_status()
                        chunks = []
                        size = 0
                        stream = response.iter_content(chunk_size=65536) if isinstance(response, Response) else response.iter_bytes(chunk_size=65536)
                        for chunk in stream:
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise HttpClientRequestError("Documento excede el límite de 25 MB.")
                            chunks.append(chunk)
                        data = self._build_response_data(response, b"".join(chunks))
                    finally:
                        response.close()
                    break
                else:
                    raise HttpClientRequestError("Demasiadas redirecciones.")
            if observer:
                observer(requested_url, data, None)
            if cache is not None:
                cache[requested_url] = data
            return data
        except (RequestException, HttpClientError, *self._httpx_errors) as exc:
            error = HttpClientTimeoutError if isinstance(exc, (Timeout, HttpClientTimeoutError, *self._httpx_timeouts)) else HttpClientRequestError
            detail = str(exc) if isinstance(exc, HttpClientError) else f"HTTP {getattr(getattr(exc, 'response', None), 'status_code', 'request failed')}"
            if observer:
                observer(requested_url, None, detail)
            raise error(detail) from exc

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def get_bytes(self, url: str, **kwargs: Any) -> bytes:
        return self.get(url, **kwargs).content

    @staticmethod
    def _build_response_data(response: Response | httpx.Response, content: bytes | None = None) -> HttpResponseData:
        content = response.content if content is None else content
        encoding = response.encoding if response.encoding and response.encoding.lower() not in ("iso-8859-1", "latin-1") else "utf-8"
        try:
            text = content.decode(encoding, errors="replace")
        except LookupError:
            text = content.decode("utf-8", errors="replace")
        return HttpResponseData(url=str(response.url), status_code=response.status_code, text=text, content=content, headers=dict(response.headers))
