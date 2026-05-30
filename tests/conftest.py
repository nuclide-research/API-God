"""Shared test fixtures and the offline fakes for the API-God suite.

No test touches the network. `fake_http` monkeypatches requests.get/post to return canned
responses routed by URL substring; `fake_ws` monkeypatches websockets.connect with a scripted
async context manager. Both confirmed against Okken, Python Testing with pytest 2e, ch10
(monkeypatch + unittest.mock, no requests-specific dependency)."""
import json, asyncio, pathlib
import pytest

TESTDATA = pathlib.Path(__file__).parent.parent / "testdata"


def load(name):
    return (TESTDATA / name).read_text()


@pytest.fixture
def td():
    """Return a loader: td('syndication_tweet.json') -> parsed dict."""
    return lambda name: json.loads(load(name))


class FakeResp:
    """Stand-in for a requests.Response. Supports .json(), .status_code, .text, .headers,
    and the streaming `with requests.get(...) as r: r.iter_content()` path fetch_meta uses."""
    def __init__(self, status=200, json_data=None, text="", raise_json=False, headers=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self._raise_json = raise_json
        self.headers = headers or {}

    def json(self):
        if self._raise_json:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, n):
        payload = json.dumps(self._json).encode() if self._json is not None else self.text.encode()
        yield payload


@pytest.fixture
def fake_http(monkeypatch):
    """install(routes, default=None): route GET/POST by URL substring to FakeResp objects."""
    def install(routes, default=None):
        def pick(url):
            for sub, resp in routes.items():
                if sub in url:
                    return resp
            if default is not None:
                return default
            raise AssertionError(f"unmocked URL: {url}")
        monkeypatch.setattr("requests.get", lambda url, *a, **k: pick(url))
        monkeypatch.setattr("requests.post", lambda url, *a, **k: pick(url))
    return install


class FakeWS:
    """Async context manager yielding scripted frames, then raising asyncio.TimeoutError."""
    def __init__(self, frames):
        self._frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, *a):
        return None

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        raise asyncio.TimeoutError()


@pytest.fixture
def fake_ws(monkeypatch):
    """install(frames): monkeypatch websockets.connect to yield those frames then time out."""
    def install(frames):
        monkeypatch.setattr("websockets.connect", lambda *a, **k: FakeWS(frames))
    return install
