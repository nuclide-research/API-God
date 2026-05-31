"""xsearch: lock the SearchTimeline parser so the drain-budget change cannot silently break it,
and verify the non-first drain budget tracks the scroll delay so slow responses are not truncated (#2)."""
import pytest
import xsearch


def test_extract_session_parses_rich_fields():
    body = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [{"content": {"itemContent": {"itemType": "TimelineTweet",
            "tweet_results": {"result": {
                "legacy": {"id_str": "1", "full_text": "hi $TST", "favorite_count": 3, "retweet_count": 1},
                "core": {"user_results": {"result": {
                    "legacy": {"screen_name": "a", "followers_count": 9},
                    "core": {"name": "A"}, "is_blue_verified": True}}}}}}}}]}]}}}}}
    recs = xsearch.extract_session(body)
    assert len(recs) == 1
    r = recs[0]
    assert r["handle"] == "@a" and r["followers"] == 9 and r["blue"] is True and r["likes"] == 3


def test_drain_budget_tracks_delay():
    assert xsearch._drain_budget(first=True, delay=1300) == 6.0
    assert xsearch._drain_budget(first=False, delay=1300) == pytest.approx(2.8)   # 1.3s + 1.5s
    assert xsearch._drain_budget(first=False, delay=300) == 2.0                    # floored at 2.0


def test_logged_in_rejects_guest_session():
    # the exact guest-only cookie set the broken --login saved on 2026-05-30: no auth_token => logged out
    guest = [{"name": n} for n in ("guest_id", "gt", "personalization_id", "__cf_bm", "g_state")]
    assert xsearch._logged_in(guest) is False


def test_logged_in_accepts_real_session():
    real = [{"name": "guest_id"}, {"name": "auth_token"}, {"name": "ct0"}, {"name": "twid"}]
    assert xsearch._logged_in(real) is True


def test_probe_report_finds_cutoff():
    # 30 good SearchTimeline responses, then X returns 429 at 24.6s -> that is the cutoff
    log = [(200, i * 0.8) for i in range(30)] + [(429, 24.6)]
    rep = xsearch._probe_report(log)
    assert rep["requests"] == 31
    assert rep["ok_before_limit"] == 30
    assert rep["limit_status"] == 429
    assert rep["limit_at_s"] == 24.6


def test_probe_report_no_limit_hit():
    # never cut off within budget -> limit_status None, all counted as ok
    log = [(200, i * 0.8) for i in range(20)]
    rep = xsearch._probe_report(log)
    assert rep["limit_status"] is None
    assert rep["ok_before_limit"] == 20


def test_extract_user_timeline_parses():
    # UserTweets body: same tweet shape as search, different timeline path (user.result.timeline_v2)
    tweet = {"result": {
        "legacy": {"id_str": "9", "full_text": "gm", "favorite_count": 5, "retweet_count": 0},
        "core": {"user_results": {"result": {
            "legacy": {"screen_name": "elonmusk", "followers_count": 100},
            "core": {"name": "Elon"}, "is_blue_verified": True}}}}}
    entry = {"content": {"itemContent": {"itemType": "TimelineTweet", "tweet_results": tweet}}}
    instructions = [{"type": "TimelineAddEntries", "entries": [entry]}]
    body = {"data": {"user": {"result": {"timeline_v2": {"timeline": {"instructions": instructions}}}}}}
    recs = xsearch.extract_user_timeline(body)
    assert len(recs) == 1
    assert recs[0]["handle"] == "@elonmusk" and recs[0]["id"] == "9" and recs[0]["likes"] == 5


def test_tweet_ts_orders_chronologically():
    older = xsearch._tweet_ts("Wed May 27 10:00:00 +0000 2026")
    newer = xsearch._tweet_ts("Sat May 30 10:00:00 +0000 2026")
    assert newer > older > 0


def test_extract_list_timeline_multi_author():
    # ListLatestTweetsTimeline body: same tweet shape, path is list.tweets_timeline; the point is
    # ONE response carries many authors (the multiplexer proven live: 22 authors / 1 call)
    def tw(sn, tid):
        return {"result": {"legacy": {"id_str": tid, "full_text": "x", "favorite_count": 1, "retweet_count": 0},
                "core": {"user_results": {"result": {"legacy": {"screen_name": sn, "followers_count": 1},
                "core": {"name": sn}, "is_blue_verified": False}}}}}
    def entry(sn, tid):
        return {"content": {"itemContent": {"itemType": "TimelineTweet", "tweet_results": tw(sn, tid)}}}
    instructions = [{"type": "TimelineAddEntries", "entries": [entry("alpha", "1"), entry("bravo", "2")]}]
    body = {"data": {"list": {"tweets_timeline": {"timeline": {"instructions": instructions}}}}}
    recs = xsearch.extract_list_timeline(body)
    assert len(recs) == 2
    assert {r["handle"] for r in recs} == {"@alpha", "@bravo"}


def test_cdn_resolve_parses(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"__typename": "Tweet", "text": "hi there", "created_at": "Sat May 30 10:00:00 +0000 2026",
                    "favorite_count": 7, "conversation_count": 2,
                    "user": {"screen_name": "zed", "name": "Zed", "is_blue_verified": True}}
    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    r = xsearch._cdn_resolve("123", "hydrate")
    assert r["id"] == "123" and r["handle"] == "@zed" and r["likes"] == 7
    assert r["source"] == ["hydrate"] and r["blue"] is True


def test_extract_batch_carries_reposts():
    # TweetResultsByRestIds body: data.tweetResult is an array; the win over the CDN is retweet_count
    body = {"data": {"tweetResult": [
        {"result": {"legacy": {"id_str": "5", "full_text": "a", "favorite_count": 9, "retweet_count": 42},
                    "core": {"user_results": {"result": {"legacy": {"screen_name": "k"}, "core": {"name": "K"}}}}}},
        {"result": {"legacy": {"id_str": "6", "full_text": "b", "favorite_count": 1, "retweet_count": 7},
                    "core": {"user_results": {"result": {"legacy": {"screen_name": "m"}, "core": {"name": "M"}}}}}},
    ]}}
    recs = xsearch.extract_batch(body)
    assert len(recs) == 2
    assert recs[0]["id"] == "5" and recs[0]["reposts"] == 42      # the field the keyless CDN drops
    assert recs[1]["reposts"] == 7 and {r["source"][0] for r in recs} == {"batch"}


def test_find_hydrate_filters_nonids(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []
    class R:
        status_code = 200
        def json(self):
            return {"__typename": "Tweet", "text": "t", "created_at": "",
                    "favorite_count": 0, "conversation_count": 0, "user": {"screen_name": "u", "name": "U"}}
    monkeypatch.setattr("requests.get", lambda url, *a, **k: (calls.append(url), R())[1])
    out = xsearch.find_hydrate(["111", "notanid", "222"], delay_ms=0)
    assert len(out) == 2 and len(calls) == 2     # bad id skipped, two real ids resolved keyless
