"""인스타 "왜 떴나"(레이더 묶음)·"왜 뜨나"(인기 오디오). 실제 API·LLM 없음."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMNotConfiguredError
from ax_contracts.schemas import AgentRequest, RequestMeta
from ax_contracts.schemas.data import InstagramAudioInput, InstagramRadarPost, MemeSearch
from data_agent import handlers
from data_agent import instagram_store as st
from data_agent.connectors import ConnectorUnavailable
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.response_cache import ResponseCache
from data_agent.skills import instagram as igs
from data_agent.skills import instagram_why as why
from data_agent.skills import topic_category, trend_age
from data_agent.skills.topic_category import CategoryResult
from data_agent.skills.trend_memes import Video

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 한국 11-18 12:00
TODAY = date(2026, 11, 18)


# ---------------------------------------------------------------- 표본


def _base(account: str, n: int = 9) -> list[dict[str, Any]]:
    """평소: 릴스 영상, 조회 1000, 좋아요 100·댓글 2, 짧은 캡션, 한국 낮(12시) 게시."""
    return [{"id": f"{account}-{i}", "media_product_type": "REELS", "media_type": "VIDEO",
             "timestamp": (NOW - timedelta(days=10 + i)).isoformat(), "view_count": 1000,
             "like_count": 100, "comments_count": 2, "caption": "평범한 글",
             "permalink": f"https://ig/{account}{i}"} for i in range(n)]  # fmt: skip


def _hot(account: str, hours_ago: float) -> dict[str, Any]:
    """터진 게시물: 캐러셀, 좋아요 5000·댓글 500, 첫 줄 질문, 긴 캡션, 한국 밤(21~24시)."""
    return {"id": f"{account}-hot", "media_product_type": "FEED", "media_type": "CAROUSEL_ALBUM",
            "timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(), "like_count": 5000,
            "comments_count": 500, "caption": "이 책 읽어 보셨어요?\n#책장털기 오늘 고른 문장",
            "permalink": f"https://ig/{account}hot"}  # fmt: skip


def _radar_media() -> dict[str, list[dict[str, Any]]]:
    # 11-17 12:00 UTC = 한국 21시, 11-17 13:00 UTC = 한국 22시 (둘 다 "밤" 구간)
    return {"a": [_hot("a", 15), *_base("a")], "b": [_hot("b", 14), *_base("b")]}


def _env(tmp_path: Path) -> dict[str, str]:
    cfg = tmp_path / "trends.toml"
    cfg.write_text('[instagram]\nradar_accounts = ["a", "b"]\n', encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(cfg), "DATA_IG_ACCESS_TOKEN": "t", "DATA_IG_USER_ID": "1",
            st.AUDIO_ENV: str(tmp_path / "ig_audio.db"),
            st.RADAR_ENV: str(tmp_path / "ig_radar.db"),
            trend_age.DB_ENV: str(tmp_path / "trend_age.db")}  # fmt: skip


def _save(env: dict[str, str], media: dict[str, list[dict[str, Any]]]) -> None:
    store = st.RadarStore(env[st.RADAR_ENV])
    for account, items in media.items():
        store.save(TODAY, account, items)
    store.close()


class FakeLLM:
    def __init__(self, line: str = "질문형 첫 줄 캐러셀이 밤에 같이 퍼짐",
                 error: Exception | None = None) -> None:  # fmt: skip
        self.line, self.error = line, error
        self.calls: list[dict[str, str]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append(dict(untrusted or {}))
        if self.error:
            raise self.error
        n = len([k for k in (untrusted or {}) if "_" not in k])
        return schema(items=[{"no": i, "description": "책 문장 캐러셀", "why_line": self.line}
                             for i in range(1, n + 1)])  # fmt: skip

    async def embed(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("no")


@pytest.fixture(autouse=True)
def _humor(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cats(topics, llm, *, cache=None, context_id=None):  # noqa: ANN001, ANN202
        return {t.keyword: CategoryResult("유머", None, 1) for t in topics}

    monkeypatch.setattr(topic_category, "classify_topics", cats)


def _radar(env: dict[str, str], llm: Any = None, **kw: Any) -> Any:
    return asyncio.run(igs.instagram_radar(llm or FakeLLM(), env=env, now=NOW,
                                           videos=kw.pop("videos", []), **kw)).output  # fmt: skip


# ---------------------------------------------------------------- 비교 규칙


def test_thresholds_are_constants() -> None:
    assert why.MIN_FACT_RATIO == 1.5 and why.MIN_SHARE_GAP == 0.20
    assert why.share_differs(0.3, 0.1)  # 20%p·3배
    assert not why.share_differs(0.5, 0.4)  # 10%p: 작음
    assert not why.share_differs(0.9, 0.65)  # 25%p지만 1.38배: 작음
    assert why.share_differs(0.25, 0.0)  # 평소 0
    assert why.ratio_differs(1.5) and why.ratio_differs(0.6) and not why.ratio_differs(1.4)
    assert not why.ratio_differs(0.7)


def test_each_kind_against_account_baseline() -> None:
    media = _radar_media()
    now = [media["a"][0], media["b"][0]]
    base = media["a"] + media["b"]  # 기준 = 묶음 계정들의 최근 게시물 전체
    facts = {(f.kind, f.label): f for f, _ in why.compare_facts(now, base)}
    fmt = facts[("format", "형식: 캐러셀 비율")]
    assert (fmt.value, fmt.baseline, fmt.ratio) == ("100%", "10%", 10.0)
    eng = facts[("engagement", "댓글률 (댓글÷좋아요, 중앙값)")]
    assert (eng.value, eng.baseline, eng.ratio) == ("10.0%", "2.0%", 5.0)
    q = facts[("caption", "첫 줄이 질문(?)인 비율")]
    assert (q.value, q.baseline) == ("100%", "10%")
    length = facts[("caption", "캡션 길이 (중앙값)")]
    assert length.value == "27자" and length.baseline == "5자"
    t = facts[("time", "올린 시간: 밤(21~24시), 한국")]
    assert (t.value, t.baseline) == ("100%", "10%")


def test_small_differences_are_dropped() -> None:
    base = _base("a", 10)
    same = [dict(base[0], id="x")]  # 평소와 같은 게시물
    assert why.compare_facts(same, base) == []


def test_missing_values_drop_the_line() -> None:
    media = _radar_media()
    now = [dict(m, comments_count=None, media_type=None, media_product_type="FEED")
           for m in (media["a"][0], media["b"][0])]  # fmt: skip
    kinds = {f.kind for f, _ in why.compare_facts(now, media["a"] + media["b"])}
    assert "engagement" not in kinds and "format" not in kinds  # 댓글·형식 모름 → 줄 없음
    assert why.compare_facts([], media["a"]) == []


def test_spread_hours() -> None:
    def post(acc: str, hours: float) -> InstagramRadarPost:
        at = NOW - timedelta(hours=hours)
        return InstagramRadarPost(account=acc, media_id=acc, metric="like_count", value=1,
                                  account_median=1, ratio=3, posted_at=at)  # fmt: skip

    fact, word = why.spread_fact([post("a", 30), post("b", 2), post("c", 5)])
    assert fact.value == "3곳 · 첫~마지막 28시간" and "며칠" in (word or "")


def test_youtube_cross_counts_channels_this_week_vs_last() -> None:
    def video(vid: str, ch: str, days: float, title: str = "오늘 #책장털기") -> Video:
        return Video(vid, title, f"ch:{ch}", ch, 100, (), NOW - timedelta(days=days))

    units = why.video_units([video("1", "x", 1), video("2", "y", 2), video("3", "x", 3),
                             video("4", "z", 9), video("5", "w", 1, "다른 영상")])  # fmt: skip
    fact, word = why.youtube_fact("#책장털기", units, NOW, True)
    assert (fact.value, fact.baseline, fact.ratio) == ("2개 채널", "1개 채널", 2.0)
    assert "늘어남" in (word or "")
    empty, _ = why.youtube_fact("#책장털기", [], NOW, False)
    assert empty.value is None and empty.reason == "유튜브 쇼츠 표본이 없음"  # 0으로 채우지 않음


# ---------------------------------------------------------------- 묶음 결과


def test_group_has_facts_line_and_fixed_unknown(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _save(env, _radar_media())
    llm = FakeLLM()
    out = _radar(env, llm)
    g = out.groups[0]
    kinds = [f.kind for f in g.why_facts]
    assert kinds[0] == "spread" and kinds[-1] == "unknown"
    assert {"format", "engagement", "caption", "time", "cross"} <= set(kinds)
    assert g.why_facts[-1].label == why.NO_AUDIO
    assert g.why_line == "질문형 첫 줄 캐러셀이 밤에 같이 퍼짐" and g.why_line_reason is None
    assert g.description == "책 문장 캐러셀"
    # LLM은 묶음 설명과 결론을 합쳐 1번. 넘긴 다른 점에는 숫자가 없다
    assert len(llm.calls) == 1
    words = [v for k, v in llm.calls[0].items() if "_다른점" in k]
    assert words and not any(ch.isdigit() for w in words for ch in w)
    # 게시물 한 줄 근거 (코드 템플릿)
    assert g.top_posts[0].standout is not None
    # 판정·배수는 그대로
    # 2026-11-19 배수 고침: 캐러셀 좋아요 5000은 좋아요 중앙값 100과 비교해 50배
    # (예전에는 릴스 조회수 1000과 섞은 중앙값과 비교해 5배로 나왔다)
    assert g.accounts == 2 and g.best_ratio == 50.0 and g.ad_material == "ok"


@pytest.mark.parametrize("error", [RuntimeError("timeout"), LLMNotConfiguredError("no key"),
                                   LLMDailyCapReached("LLM 하루 상한에 닿아 (토큰)")])  # fmt: skip
def test_llm_failure_or_cap_keeps_facts(tmp_path: Path, error: Exception) -> None:
    env = _env(tmp_path)
    _save(env, _radar_media())
    g = _radar(env, FakeLLM(error=error)).groups[0]
    assert g.why_line is None and g.why_line_reason and "결론을 쓰지 못함" in g.why_line_reason
    assert len(g.why_facts) >= 5  # 근거 숫자는 그대로
    if isinstance(error, LLMDailyCapReached):
        assert "상한" in g.why_line_reason


def test_llm_line_with_digits_is_dropped(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _save(env, _radar_media())
    g = _radar(env, FakeLLM(line="댓글이 5배 많아서 뜸")).groups[0]
    assert g.why_line is None and "숫자" in (g.why_line_reason or "")
    long = _radar(env, FakeLLM(line="가" * 121)).groups[0]
    assert long.why_line is None and "길어" in (long.why_line_reason or "")


def test_naver_failure_only_that_line(tmp_path: Path) -> None:
    class Down:
        calls_today = 0

        async def search_trend(self, query):  # noqa: ANN001, ANN201
            raise ConnectorUnavailable("UNAVAILABLE", "down")

    class Boom:
        async def search_trend(self, query):  # noqa: ANN001, ANN201
            raise RuntimeError("x")

    env = _env(tmp_path)
    _save(env, _radar_media())
    for connector in (Down(), Boom()):
        g = _radar(env, connector=connector).groups[0]
        naver = [f for f in g.why_facts if f.label.startswith("네이버")]
        assert len(naver) == 1 and naver[0].value is None and naver[0].reason
        assert g.why_line is not None and any(f.kind == "format" for f in g.why_facts)


def test_naver_rising_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ratios(names, connector, env, now, *, age_store=None):  # noqa: ANN001, ANN202
        return [MemeSearch(status="rising", ratio=2.4)] * len(names), len(names), []

    from data_agent.skills import trend_memes

    monkeypatch.setattr(trend_memes, "search_ratios", ratios)
    env = _env(tmp_path)
    _save(env, _radar_media())
    g = _radar(env, connector=object()).groups[0]
    naver = next(f for f in g.why_facts if f.label.startswith("네이버"))
    assert (naver.value, naver.ratio) == ("상승", 2.4)


def test_one_llm_call_per_cycle_through_the_response_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV
    from data_agent.youtube_store import DB_ENV as YT_ENV

    monkeypatch.setenv(SHORTS_DB_ENV, str(tmp_path / "none-shorts.db"))
    monkeypatch.setenv(YT_ENV, str(tmp_path / "none-yt.db"))
    _save(env, _radar_media())
    store = st.RadarStore(env[st.RADAR_ENV])
    store.complete(TODAY, NOW, 2)
    store.close()
    llm = FakeLLM()
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache(tmp_path / "responses.db"))
    monkeypatch.setattr(handlers, "get_llm", lambda: llm)
    monkeypatch.setattr(handlers, "get_category_cache", lambda: None)
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: None)
    meta = RequestMeta(context_id="ctx_ig", token="x",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    request = AgentRequest(meta=meta, skill="instagram_radar", input={})
    first = asyncio.run(handlers.instagram_radar(request))
    asyncio.run(handlers.instagram_radar(request))
    assert len(llm.calls) == 1  # 같은 바퀴: 저장된 답
    assert first.output["groups"][0]["why_line"]
    store = st.RadarStore(env[st.RADAR_ENV])
    store.complete(TODAY, NOW + timedelta(hours=1), 2)  # 바퀴를 다시 다 돎 → 새로 계산
    store.close()
    asyncio.run(handlers.instagram_radar(request))
    assert len(llm.calls) == 2


# ---------------------------------------------------------------- 게시물 한 줄


def test_standout_picks_the_biggest_difference() -> None:
    media = _radar_media()["a"]
    line = why.standout(media[0], media)
    assert line is not None and "이 계정 평소 0%만 캐러셀" in line  # 형식·질문·시간이 평소 0%
    plain = why.standout(media[1], media)  # 평소와 같은 게시물
    assert plain is None


def test_standout_engagement_template() -> None:
    base = _base("a", 10)
    post = dict(base[0], id="p", comments_count=40)  # 댓글률 40% vs 평소 2%
    assert why.standout(post, base) == "댓글률 40.0% · 평소 2.0%"


# ---------------------------------------------------------------- 인기 오디오


def test_audio_title_matching_avoids_short_title_false_hits() -> None:
    def video(vid: str, title: str, tags: tuple[str, ...] = ()) -> Video:
        return Video(vid, title, f"ch:{vid}", vid, 1, tags, NOW - timedelta(days=1))

    units = why.video_units([
        video("1", "봄날 커버 챌린지"), video("2", "오늘 봄 산책"), video("3", "아이유 봄 커버"),
        video("4", "x", ("Winter Piano",)),
    ])  # fmt: skip
    long = why.title_hits("봄날 커버", None, units)
    assert long is not None and len(long) == 1
    assert why.title_hits("봄", None, units) is None  # 짧은 곡명 + 가수 없음: 세지 않음
    short = why.title_hits("봄", "아이유", units)
    assert short is not None and len(short) == 1  # "오늘 봄 산책"은 세지 않음
    tagged = why.title_hits("winter piano", None, units)
    assert tagged is not None and len(tagged) == 1  # 태그도 본다 (정규화)


def test_audio_why_facts(tmp_path: Path) -> None:
    env = _env(tmp_path)
    store = st.AudioStore(env[st.AUDIO_ENV])
    week_ago = TODAY - timedelta(days=7)
    store.save(week_ago, "music", [{"audio_id": "b", "title": "봄날 커버"},
                                   {"audio_id": "a", "title": "Winter Piano"}])  # fmt: skip
    store.save(TODAY, "music", [{"audio_id": "a", "title": "Winter Piano", "artist": "X"},
                                {"audio_id": "c", "title": "봄", "artist": None}])  # fmt: skip
    store.log("ig_audio", TODAY, "connected", None, NOW)
    store.close()
    out = igs.instagram_audio(InstagramAudioInput(audio_type="music"), env=env, now=NOW).output
    videos = [Video("1", "winter piano 챌린지", "ch:1", "1", 1, (), NOW - timedelta(days=2)),
              Video("2", "Winter Piano", "ch:2", "2", 1, (), NOW - timedelta(days=9))]  # fmt: skip
    asyncio.run(why.audio_why(out, env, NOW, videos=videos))
    a, c = out.items
    facts = {f.kind: f for f in a.why_facts}
    assert (facts["rank"].value, facts["rank"].baseline) == ("1위", "2위")
    assert (facts["youtube"].value, facts["youtube"].baseline) == ("1개", "1개")
    assert facts["search"].value is None and facts["search"].reason == "네이버 커넥터가 없음"
    assert facts["unknown"].label == why.AUDIO_USE_UNKNOWN
    c_facts = {f.kind: f for f in c.why_facts}
    assert c_facts["rank"].baseline == "7일 전 목록에 없음"
    assert c_facts["youtube"].value is None and "가수" in (c_facts["youtube"].reason or "")


def test_audio_search_only_top_five(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def ratios(names, connector, env, now, *, age_store=None):  # noqa: ANN001, ANN202
        seen.append([n for n, _ in names])
        return [MemeSearch(status="normal", ratio=1.0)] * len(names), len(names), []

    from data_agent.skills import trend_memes

    monkeypatch.setattr(trend_memes, "search_ratios", ratios)
    env = _env(tmp_path)
    store = st.AudioStore(env[st.AUDIO_ENV])
    songs = [{"audio_id": f"a{i}", "title": f"곡 이름 {i}"} for i in range(7)]
    store.save(TODAY, "music", songs)
    store.close()
    out = igs.instagram_audio(InstagramAudioInput(audio_type="music"), env=env, now=NOW).output
    asyncio.run(why.audio_why(out, env, NOW, connector=object(), videos=[]))
    assert seen == [[f"곡 이름 {i}" for i in range(5)]]
    sixth = {f.kind: f for f in out.items[5].why_facts}["search"]
    assert sixth.value is None and "상위 5곡" in (sixth.reason or "")
    first = {f.kind: f for f in out.items[0].why_facts}
    assert first["search"].value == "보통"
    assert first["rank"].reason == "7일 전 목록이 없음 (수집 기록 부족)"  # 모름 + 이유
