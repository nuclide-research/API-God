"""The consolidated engine_core.resolve_tweet (finding #11): status branching, fully offline."""
import engine_core
from conftest import FakeResp


def test_resolve_ok(fake_http, td):
    fake_http({"tweet-result": FakeResp(200, td("syndication_tweet.json"))})
    st, d = engine_core.resolve_tweet("123")
    assert st == "ok" and d["user"]["screen_name"] == "realhandle"


def test_resolve_tombstone(fake_http, td):
    fake_http({"tweet-result": FakeResp(200, td("syndication_tombstone.json"))})
    assert engine_core.resolve_tweet("1")[0] == "tombstone"


def test_resolve_404(fake_http):
    fake_http({"tweet-result": FakeResp(404, None, text="<html>not found</html>")})
    assert engine_core.resolve_tweet("1")[0] == "404"


def test_resolve_malformed(fake_http):
    fake_http({"tweet-result": FakeResp(200, None, raise_json=True)})
    assert engine_core.resolve_tweet("1")[0] == "notjson"
