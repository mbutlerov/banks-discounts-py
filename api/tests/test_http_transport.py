"""Opt-in HTTPX fallback keeps the bounded, auditable acquisition contract."""
from collections import deque
from io import BytesIO

import httpx
import pytest
import requests

from app.scraping.clients import http_client
from app.scraping.clients.http_client import (
    HttpClient, HttpClientRequestError, HttpClientTimeoutError, HttpResponseData, http_scope,
)

GNB_HOST = "www.beneficiosbancognb.com.py"
GNB_URL = f"https://{GNB_HOST}/v2/beneficios"
BOT_UA = "BanksDiscounts/1.0 (+public-benefit-catalog)"


def response(status=200, content=b"requests result", *, headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {"Content-Type": "text/html; charset=utf-8"})
    result.url = GNB_URL
    result.encoding = "utf-8"
    result.raw = BytesIO(content)
    return result


class Session:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        result.url = url
        return result

    def close(self):
        self.closed = True


def fallback_client(session, handler, **kwargs):
    transport = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return HttpClient(session=session, httpx_fallback_hosts=(GNB_HOST,), httpx_client=transport, **kwargs)


def test_403_fallback_preserves_request_and_observes_only_final_response_and_cache():
    blocked = response(403, b"Access denied")
    session = Session(blocked)
    requests_seen, observations = [], []

    def serve(request):
        requests_seen.append(request)
        return httpx.Response(200, content="Catálogo GNB".encode(), headers={"Content-Type": "text/html; charset=utf-8"})

    client = fallback_client(session, serve, timeout=21)
    with http_scope(observer=lambda *values: observations.append(values)):
        result = client.get(GNB_URL, params={"category": "gastronomía"}, headers={"Accept": "application/json"}, timeout=7)
        repeated = client.get(GNB_URL, params={"category": "gastronomía"}, headers={"Accept": "application/json"}, timeout=7)

    assert repeated is result
    assert result.text == "Catálogo GNB" and result.content == "Catálogo GNB".encode()
    assert result.url == session.calls[0][0] == str(requests_seen[0].url)
    assert requests_seen[0].headers["User-Agent"] == session.calls[0][1]["headers"]["User-Agent"] == BOT_UA
    assert requests_seen[0].headers["Accept"] == "application/json"
    assert requests_seen[0].extensions["timeout"] == {"connect": 7, "read": 7, "write": 7, "pool": 7}
    assert session.calls[0][1]["allow_redirects"] is False and session.calls[0][1]["stream"] is True
    assert len(requests_seen) == len(observations) == 1
    assert observations[0] == (result.url, result, None)
    assert blocked.raw.closed
    client.close()
    assert session.closed and client._httpx_client.is_closed


@pytest.mark.parametrize("status,host,opt_in", [
    (401, GNB_HOST, True), (500, GNB_HOST, True), (403, GNB_HOST, False),
    (403, "www.ueno.com.py", True), (403, "beneficiosbancognb.com.py", True),
    (403, "api.www.beneficiosbancognb.com.py", True),
])
def test_fallback_only_for_exact_opted_in_host_and_403(monkeypatch, status, host, opt_in):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("HTTPX should not be created"))
    client = HttpClient(session=Session(response(status)), httpx_fallback_hosts=(GNB_HOST,) if opt_in else ())
    with pytest.raises(HttpClientRequestError, match=f"HTTP {status}"):
        client.get(f"https://{host}/v2/beneficios")


def test_successful_requests_never_initializes_httpx(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("Successful requests does not need HTTPX"))
    client = HttpClient(session=Session(response()), httpx_fallback_hosts=(GNB_HOST,))
    assert client.get_text(GNB_URL) == "requests result"


def test_opted_in_fallback_uses_standard_httpx_user_agent_and_preserves_other_headers():
    seen = []

    def serve(request):
        seen.append(request)
        return httpx.Response(200, content=b"catalogue")

    session = Session(response(403))
    client = fallback_client(session, serve, httpx_default_user_agent_on_fallback=True)
    client.get(GNB_URL, headers={"user-agent": "BanksDiscounts/custom-public-client", "Accept": "application/json", "X-Catalogue": "public"})
    assert session.calls[0][1]["headers"]["user-agent"] == "BanksDiscounts/custom-public-client"
    assert seen[0].headers["User-Agent"] == f"python-httpx/{httpx.__version__}"
    assert seen[0].headers["Accept"] == "application/json"
    assert seen[0].headers["X-Catalogue"] == "public"
    client.close()


def test_default_fallback_preserves_custom_user_agent():
    seen = []

    def serve(request):
        seen.append(request)
        return httpx.Response(200, content=b"catalogue")

    session = Session(response(403))
    client = fallback_client(session, serve, httpx_default_user_agent_on_fallback=False)
    client.get(GNB_URL, headers={"User-Agent": "BanksDiscounts/custom-public-client", "Accept": "application/json"})
    assert seen[0].headers["User-Agent"] == session.calls[0][1]["headers"]["User-Agent"] == "BanksDiscounts/custom-public-client"
    assert seen[0].headers["Accept"] == "application/json"
    client.close()


def test_replay_never_initializes_or_calls_either_transport(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("Replay initialized HTTPX"))
    session = Session()
    client = HttpClient(session=session, httpx_fallback_hosts=(GNB_HOST,))
    stored = HttpResponseData(GNB_URL, 200, "stored catalogue", b"stored catalogue", {})
    with http_scope(replay={GNB_URL: stored}):
        assert client.get(GNB_URL) is stored
        with pytest.raises(HttpClientRequestError, match="ausente del replay"):
            client.get(GNB_URL + "/missing")
    assert not session.calls and client._httpx_client is None


def test_httpx_failure_is_not_observed_as_a_success_or_cached():
    observations, fallback_seen = [], []

    def serve(request):
        fallback_seen.append(request)
        return httpx.Response(403, content=b"Access denied")

    client = fallback_client(Session(response(403), response(403)), serve)
    with http_scope(observer=lambda *values: observations.append(values)):
        for _ in range(2):
            with pytest.raises(HttpClientRequestError, match="HTTP 403"):
                client.get(GNB_URL)
    assert len(fallback_seen) == len(observations) == 2
    assert all(item == (GNB_URL, None, "HTTP 403") for item in observations)
    client.close()


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("slow connect"), httpx.ReadTimeout("slow read")])
def test_httpx_timeouts_use_shared_error_type(error):
    def serve(request):
        raise error

    client = fallback_client(Session(response(403)), serve)
    with pytest.raises(HttpClientTimeoutError):
        client.get(GNB_URL)
    client.close()


def test_httpx_network_error_uses_shared_error_type():
    def serve(request):
        raise httpx.ConnectError("unavailable")

    client = fallback_client(Session(response(403)), serve)
    with pytest.raises(HttpClientRequestError, match="HTTP request failed"):
        client.get(GNB_URL)
    client.close()


class BodyStream(httpx.SyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("body,error", [
    ((b"12345", b"67890"), HttpClientRequestError),
    ((b"start", httpx.ReadTimeout("slow body")), HttpClientTimeoutError),
])
def test_httpx_stream_is_bounded_and_closed_on_body_failure(monkeypatch, body, error):
    monkeypatch.setattr(http_client, "MAX_RESPONSE_BYTES", 8)
    stream = BodyStream(*body)
    client = fallback_client(Session(response(403)), lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(error):
        client.get(GNB_URL)
    assert stream.closed
    client.close()


@pytest.mark.parametrize("target", ["https://evil.example/catalogue", "https://user:password@www.beneficiosbancognb.com.py/private"])
def test_httpx_redirects_are_validated_before_following(target):
    fallback_seen = []

    def serve(request):
        fallback_seen.append(request)
        return httpx.Response(302, headers={"Location": target})

    session = Session(response(403))
    client = fallback_client(session, serve)
    with pytest.raises(HttpClientRequestError, match="fuentes bancarias permitidas"):
        client.get(GNB_URL)
    assert len(session.calls) == len(fallback_seen) == 1
    client.close()


def test_httpx_allowed_redirect_uses_shared_hop_validation_and_only_one_observation():
    fallback_seen, observations = [], []

    def serve(request):
        fallback_seen.append(request)
        if request.url.path == "/v2/beneficios":
            return httpx.Response(302, headers={"Location": "/v2/catalogue"})
        return httpx.Response(200, content=b"redirected catalogue")

    session = Session(response(403), response(403))
    client = fallback_client(session, serve)
    with http_scope(observer=lambda *values: observations.append(values)):
        result = client.get(GNB_URL)
    assert result.url == f"https://{GNB_HOST}/v2/catalogue"
    assert result.text == "redirected catalogue"
    assert len(session.calls) == len(fallback_seen) == 2
    assert observations == [(GNB_URL, result, None)]
    client.close()


def test_httpx_unfollowed_redirect_matches_requests_semantics():
    client = fallback_client(Session(response(403)), lambda request: httpx.Response(302, headers={"Location": "/v2/catalogue"}, content=b"redirect"))
    result = client.get(GNB_URL, allow_redirects=False)
    assert result.status_code == 302 and result.content == b"redirect"
    client.close()


def test_httpx_redirect_loop_stops_after_shared_limit():
    session = Session(*(response(403) for _ in range(6)))
    client = fallback_client(session, lambda request: httpx.Response(302, headers={"Location": "/v2/beneficios"}))
    with pytest.raises(HttpClientRequestError, match="Demasiadas redirecciones"):
        client.get(GNB_URL)
    assert len(session.calls) == 6
    client.close()
