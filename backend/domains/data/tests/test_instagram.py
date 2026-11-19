"""인스타그램: Graph API 커넥터·수집·오디오·레이더·해시태그 칸·메모. MockTransport·가짜 커넥터만."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import RequestMeta
from ax_contracts.schemas.data import (
    InstagramAudioInput,
    InstagramMemoAddInput,
    InstagramMemoListInput,
    TrendMeme,
)
from data_agent import instagram_store as st
from data_agent.connectors import instagram_graph as ig
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import instagram as igs
from data_agent.skills import topic_category
from data_agent.skills.topic_category import CategoryResult

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 한국 11-18
TODAY = date(2026, 11, 18)
ENV_KEYS = {"DATA_IG_ACCESS_TOKEN": "secret-token-123", "DATA_IG_USER_ID": "1784"}


def _graph(handler, env=None, limit=None) -> ig.InstagramGraph:  # noqa: ANN001
    env = {**ENV_KEYS, **(env or {})}
    if limit:
        env["DATA_IG_DAILY_LIMIT"] = str(limit)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ig.InstagramGraph(env, client=client, calls=JsonDailyCache())


# ---------------------------------------------------------------- 커넥터


def test_connector_sends_get_only() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith("/ig_audio"):
            return httpx.Response(200, json={"audio": []})
        if request.url.path.endswith("/ig_hashtag_search"):
            return httpx.Response(200, json={"data": [{"id": "h1"}]})
        if request.url.path.endswith("/recent_media"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"business_discovery": {"media": {"data": [{"id": "m"}]}}})

    g = _graph(handler)
    asyncio.run(g.audio("music"))
    asyncio.run(g.business_media("bluebottle"))
    asyncio.run(g.hashtag_id("책"))
    asyncio.run(g.hashtag_media("h1", "recent_media", ("id",)))
    assert methods and set(methods) == {"GET"}
    # 게시·삭제 경로가 없다 (코드에 POST·DELETE 호출이 없음)
    source = inspect.getsource(ig)
    for word in (".post(", ".delete(", ".put(", ".patch(", "media_publish", "comment_enabled"):
        assert word not in source


def test_audio_request_and_parse_drops_download_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"audio": [
            {"audio_id": "a1", "title": "Winter Piano", "display_artist": "X",
             "duration_in_ms": 32000, "download_url": "https://expire",
             "on_platform_audio_preview_link": "https://www.instagram.com/a1"},
            {"audio_id": "a2", "title": "원본", "ig_username": "maker", "duration_in_ms": 18000},
        ]})  # fmt: skip

    items = asyncio.run(_graph(handler).audio("original_sound"))
    q = seen[0].url
    assert q.host == "graph.facebook.com" and q.path == "/v23.0/ig_audio"
    assert q.params["audio_type"] == "original_sound" and q.params["user_id"] == "1784"
    assert "search_query" not in q.params
    assert items[0] == {"audio_id": "a1", "title": "Winter Piano", "artist": "X",
                        "duration_ms": 32000, "link": "https://www.instagram.com/a1"}  # fmt: skip
    assert items[1]["artist"] == "maker" and "download_url" not in items[0]


def test_business_discovery_fields_and_empty_is_no_data() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"business_discovery": {"media": {"data": []}}})

    with pytest.raises(ig.IgError) as err:
        asyncio.run(_graph(handler).business_media("agelimited", 20))
    assert err.value.code == "NO_DATA" and "데이터 없음" in str(err.value)
    fields = seen[0].url.params["fields"]
    assert fields.startswith("business_discovery.username(agelimited){username,media.limit(20){")
    # 2026-11-18 사양 변경: 썸네일용 media_type·media_url·thumbnail_url을 요청한다 (호출 수는 같음)
    # 2026-11-19: 계정 표시 이름(name)도 같은 호출에서 받는다 (호출 수 그대로)
    assert fields.endswith(
        "{id,media_product_type,timestamp,like_count,comments_count,view_count,caption,"
        "permalink,media_type,media_url,thumbnail_url},name}"
    )


def test_business_media_attaches_account_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"business_discovery": {
            "name": " 톤28 ", "media": {"data": [{"id": "1"}, {"id": "2"}]}}})  # fmt: skip

    got = asyncio.run(_graph(handler).business_media("toun28_official", 20))
    assert [m[ig.OWNER_NAME] for m in got] == ["톤28", "톤28"]

    def nameless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"business_discovery": {"media": {"data": [{"id": "1"}]}}})

    assert ig.OWNER_NAME not in asyncio.run(_graph(nameless).business_media("x", 20))[0]


@pytest.mark.parametrize(("code", "want"), [
    (190, "TOKEN_EXPIRED"), (10, "PERMISSION"), (200, "PERMISSION"), (299, "PERMISSION"),
    (4, "RATE_LIMITED"), (17, "RATE_LIMITED"), (32, "RATE_LIMITED"), (613, "RATE_LIMITED"),
])  # fmt: skip
def test_error_codes_and_token_never_leaks(code: int, want: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": code, "type": "OAuthException",
                                                   "message": "Meta says no"}})  # fmt: skip

    g = _graph(handler)
    with pytest.raises(ig.IgError) as err:
        asyncio.run(g.audio("music"))
    assert err.value.code == want
    assert "secret-token-123" not in str(err.value) and "secret-token-123" not in repr(g)
    if want == "PERMISSION":
        assert ig.PERMISSION_NOTE in str(err.value)
    if want == "RATE_LIMITED":
        assert str(err.value).startswith("한도")


def test_no_key_and_daily_limit() -> None:
    g = ig.InstagramGraph({}, calls=JsonDailyCache())
    with pytest.raises(ig.IgError) as err:
        asyncio.run(g.audio("music"))
    assert err.value.code == "KEY_MISSING" and "키 등록 후 가능" in str(err.value)
    one = _graph(lambda r: httpx.Response(200, json={"audio": []}), limit=1)
    asyncio.run(one.audio("music"))
    with pytest.raises(Exception) as err2:
        asyncio.run(one.audio("music"))
    assert getattr(err2.value, "code", "") == "RATE_LIMITED"


def test_hashtag_pages_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        paging = {"cursors": {"after": "X"}, "next": "https://graph.facebook.com/n"}
        return httpx.Response(200, json={"data": [{"id": str(i)} for i in range(50)],
                                         "paging": paging})

    media, more = asyncio.run(_graph(handler).hashtag_media("h", "recent_media", ("id",), 2))
    assert len(media) == 100 and more


# ---------------------------------------------------------------- 수집


class FakeGraph:
    configured = True

    def __init__(self, audio_error: Exception | None = None,
                 media: dict[str, Any] | None = None) -> None:  # fmt: skip
        self.audio_error, self.media = audio_error, media or {}
        self.calls_today = 0
        self.asked: list[str] = []

    async def audio(self, kind):  # noqa: ANN001, ANN201
        self.calls_today += 1
        if self.audio_error:
            raise self.audio_error
        item = {"audio_id": f"{kind}-1", "title": "t", "artist": "a", "duration_ms": 1}
        return [{**item, "link": None}]

    async def business_media(self, account, limit=20):  # noqa: ANN001, ANN201
        self.calls_today += 1
        self.asked.append(account)
        got = self.media.get(account)
        if isinstance(got, Exception):
            raise got
        return got or []


def _config(tmp_path: Path, body: str = "") -> dict[str, str]:
    cfg = tmp_path / "trends.toml"
    cfg.write_text(body, encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(cfg), **ENV_KEYS,
            st.AUDIO_ENV: str(tmp_path / "ig_audio.db"),
            st.RADAR_ENV: str(tmp_path / "ig_radar.db"),
            st.HASHTAG_ENV: str(tmp_path / "ig_hashtags.db"),
            st.MEMO_ENV: str(tmp_path / "ig_memo.db")}  # fmt: skip


def _media(n: int, base: int, hot: int | None = None, caption: str = "") -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        at = (NOW - timedelta(days=1 if i == 0 else 10 + i)).isoformat().replace("+00:00", "+0000")
        out.append({"id": f"{base}-{i}", "media_product_type": "REELS", "timestamp": at,
                    "view_count": hot if (i == 0 and hot) else 1000, "like_count": 10,
                    "caption": caption if i == 0 else "평범한 글",
                    "permalink": f"https://ig/{i}"})  # fmt: skip
    return out


def test_collect_audio_permission_does_not_stop_radar(tmp_path: Path) -> None:
    env = _config(tmp_path, '[instagram]\nradar_accounts = ["a", "b", "c"]\n')
    graph = FakeGraph(
        audio_error=ig.IgError("PERMISSION", "권한"),
        media={"a": _media(20, 1), "b": ig.IgError("NO_DATA", "데이터 없음 (나이 제한 등)"),
               "c": _media(20, 3)},
    )  # fmt: skip
    got = asyncio.run(igs.collect(env, graph, now=NOW))
    assert got.audio == {"music": "permission", "original_sound": "permission"}
    assert got.accounts_read == 2 and got.accounts_failed == 1 and graph.asked == ["a", "b", "c"]
    out = igs.instagram_audio(InstagramAudioInput(), env=env, now=NOW).output
    assert out.status == "unknown" and out.reason == ig.AUDIO_PERMISSION_NOTE and out.items == []
    by = {s.source_id: s for s in out.sources}
    assert by["ig_audio"].status == "permission"
    assert by["ig_business_discovery"].status == "connected"
    _, media, failed = st.radar_latest(env[st.RADAR_ENV])
    assert set(media) == {"a", "c"} and "데이터 없음" in failed["b"]


def test_run_without_key_writes_nothing(tmp_path: Path) -> None:
    env = {st.AUDIO_ENV: str(tmp_path / "a.db"), st.RADAR_ENV: str(tmp_path / "r.db")}
    assert asyncio.run(igs.run(env)) is None
    assert not (tmp_path / "a.db").exists() and not (tmp_path / "r.db").exists()


# ---------------------------------------------------------------- 오디오 변화


def test_audio_changes_rank_new_streak() -> None:
    def row(r: int, a: str) -> dict[str, Any]:
        return {"rank": r, "audio_id": a, "title": a}

    days = {TODAY - timedelta(days=2): [row(1, "x")],
            TODAY - timedelta(days=1): [row(1, "x"), row(2, "y")],
            TODAY: [row(1, "y"), row(2, "x"), row(3, "z")]}  # fmt: skip
    items, prev = igs.audio_changes(days, TODAY)
    by = {i.audio_id: i for i in items}
    assert prev == TODAY - timedelta(days=1)
    assert by["y"].rank_change == 1 and by["x"].rank_change == -1 and by["z"].is_new
    assert by["x"].streak_days == 3 and by["y"].streak_days == 2 and by["z"].streak_days == 1


# ---------------------------------------------------------------- 레이더


class FakeLLM:
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        n = len([k for k in (untrusted or {}) if "_" not in k])
        line = "책 표지를 뒤집는 형식"
        return schema(items=[{"no": i, "description": line} for i in range(1, n + 1)])

    async def embed(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("no")


def test_radar_bursts_grouped_across_accounts(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    async def cats(topics, llm, *, cache=None, context_id=None):  # noqa: ANN001, ANN202
        # 광고 재료는 공용 규칙 그대로 (기본 허용 분류인 유머로 둔다)
        return {t.keyword: CategoryResult("유머", None, 1) for t in topics}

    monkeypatch.setattr(topic_category, "classify_topics", cats)
    env = _config(tmp_path, '[instagram]\nradar_accounts = ["a", "b", "c"]\n')
    store = st.RadarStore(env[st.RADAR_ENV])
    store.save(TODAY, "a", _media(20, 1, hot=5000, caption="책 한 줄 #책장털기"))
    store.save(TODAY, "b", _media(20, 2, hot=4000, caption="오늘의 책 #책장털기"))
    store.save(TODAY, "c", _media(20, 3, hot=None, caption=""))  # 터짐 없음
    store.close()
    out = asyncio.run(igs.instagram_radar(FakeLLM(), env=env, now=NOW)).output
    assert out.status == "known" and out.bursts == 2 and out.accounts_read == 3
    g = out.groups[0]
    assert g.name == "#책장털기" and g.accounts == 2 and g.best_ratio == 5.0
    assert g.description == "책 표지를 뒤집는 형식" and g.ad_material == "ok"
    assert g.top_posts[0].metric == "view_count" and g.top_posts[0].account_median == 1000


def test_radar_without_accounts_says_why(tmp_path: Path) -> None:
    out = asyncio.run(igs.instagram_radar(FakeLLM(), env=_config(tmp_path), now=NOW)).output
    assert out.status == "unknown" and "radar_accounts" in (out.reason or "")


def test_old_post_is_not_a_burst() -> None:
    media = {"a": _media(20, 1, hot=9000)}
    media["a"][0]["timestamp"] = (NOW - timedelta(days=9)).isoformat()
    assert igs.bursts(media, NOW, igs.IgConfig()) == []


# ---------------------------------------------------------------- 해시태그 칸


class FakeTags:
    configured = True

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.ids: list[str] = []

    async def hashtag_id(self, name):  # noqa: ANN001, ANN201
        if self.error:
            raise self.error
        self.ids.append(name)
        return f"id-{name}"

    async def hashtag_media(self, hid, edge, fields, pages=1):  # noqa: ANN001, ANN201
        if edge == "recent_media":
            at = (NOW - timedelta(hours=2)).isoformat()
            old = (NOW - timedelta(hours=30)).isoformat()
            return [{"id": "1", "timestamp": at}, {"id": "2", "timestamp": old}], False
        return [{"id": "t", "like_count": 100}, {"id": "u", "like_count": 300}], False


def _memes() -> list[TrendMeme]:
    return [TrendMeme(name="#책장털기", kind="hashtag", channels=3, videos=5),
            TrendMeme(name="이거 나만 몰랐어", kind="phrase", channels=3, videos=5)]  # fmt: skip


def test_fill_instagram_hashtag_only_and_day_cache(tmp_path: Path) -> None:
    env = _config(tmp_path)
    items = _memes()
    tags = FakeTags()
    asyncio.run(igs.fill_instagram(items, tags, env, NOW))
    first, phrase = items
    assert first.instagram.status == "known" and first.instagram.posts == 1
    assert first.instagram.top_like_median == 200
    assert phrase.instagram.status == "not_hashtag" and phrase.instagram.reason == "해시태그 아님"
    again = _memes()
    asyncio.run(igs.fill_instagram(again, tags, env, NOW))
    assert tags.ids == ["책장털기"] and again[0].instagram.posts == 1  # 그날 결과 재사용


def test_fill_instagram_stops_new_queries_near_weekly_limit(tmp_path: Path) -> None:
    env = _config(tmp_path)
    store = st.HashtagStore(env[st.HASHTAG_ENV])
    for i in range(25):  # 30 - 25 = 5 ≤ 예비 5
        store.use(f"tag{i}", f"id{i}", NOW - timedelta(days=1))
    items = _memes()
    tags = FakeTags()
    asyncio.run(igs.fill_instagram(items, tags, env, NOW, store=store))
    store.close()
    assert tags.ids == [] and items[0].instagram.status == "not_checked"
    assert items[0].instagram.reason == "이번 주 한도 소진(30개 중 25개 사용)"


def test_fill_instagram_permission_is_unknown_not_zero(tmp_path: Path) -> None:
    items = _memes()
    asyncio.run(igs.fill_instagram(items, FakeTags(ig.IgError("PERMISSION", "x")),
                                   _config(tmp_path), NOW))  # fmt: skip
    assert items[0].instagram.status == "unknown" and items[0].instagram.posts is None
    assert items[0].instagram.reason == ig.PERMISSION_NOTE


# ---------------------------------------------------------------- 메모


@pytest.fixture
def meta() -> RequestMeta:
    return RequestMeta(context_id="ctx_test", token="t", deadline=NOW + timedelta(minutes=1))


class Decision:
    def __init__(self, status: str) -> None:
        self.status = status


def test_memo_add_disabled_until_registered(tmp_path: Path, meta: RequestMeta) -> None:
    env = _config(tmp_path)
    calls: list[str] = []

    async def gate(action, *a):  # noqa: ANN001, ANN002, ANN202
        calls.append(action)
        return Decision("executed")

    req = InstagramMemoAddInput(kind="audio", name="Winter Piano", author="재현")
    out = asyncio.run(igs.memo_add(req, meta, env=env, gate=gate, now=NOW))
    assert out.outcome == "rejected" and "safety 등록" in (out.reason or "") and calls == []
    listed = igs.memo_list(InstagramMemoListInput(), env=env)
    assert listed.can_add is False and listed.items == []


def test_memo_add_through_gate(tmp_path: Path, meta: RequestMeta) -> None:
    env = _config(tmp_path, "[instagram]\nmemo_add_enabled = true\n")
    seen: list[tuple[str, str | None]] = []

    async def gate(action, params, context, evidence):  # noqa: ANN001, ANN202
        seen.append((action, context.idempotency_key))
        return Decision("executed")

    req = InstagramMemoAddInput(kind="format", name="사진 덤프 10장", memo="첫 장 큰 글씨",
                                author="재헌", linked_trend="#연말결산")  # fmt: skip
    out = asyncio.run(igs.memo_add(req, meta, env=env, gate=gate, now=NOW))
    assert out.outcome == "added" and out.memo is not None and out.memo.source == "manual"
    assert seen[0][0] == "add_instagram_memo" and seen[0][1]
    listed = igs.memo_list(InstagramMemoListInput(), env=env)
    assert listed.can_add and [m.name for m in listed.items] == ["사진 덤프 10장"]

    async def refuse(*a):  # noqa: ANN002, ANN202
        return Decision("rejected")

    no = asyncio.run(igs.memo_add(req, meta, env=env, gate=refuse, now=NOW))
    assert no.outcome == "rejected"
    assert len(igs.memo_list(InstagramMemoListInput(), env=env).items) == 1
