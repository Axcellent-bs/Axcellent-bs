"""트렌드 분석 의뢰 보고서 (trend_report_*). 실제 API·LLM 없음."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template.llm import LLMNotConfiguredError
from ax_contracts.schemas import AgentRequest, EventType, RequestMeta
from ax_contracts.schemas.data import (
    InstagramRadarPost,
    TrendKeywordGroup,
    TrendReport,
    TrendReportInput,
    TrendScanInput,
)
from ax_contracts.schemas.event import EVENT_DATA_MODELS, TrendReportReadyData
from data_agent import handlers
from data_agent import instagram_store as st
from data_agent.connectors import ConnectorUnavailable, naver_datalab, naver_shopping
from data_agent.connectors.instagram_graph import IgError
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.skills import trend_age
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_runner import TrendReportService, ready_event
from data_agent.trend_report_store import INTERRUPTED_REASON, TrendReportStore

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)  # 한국 11-19 12:00
TODAY = date(2026, 11, 19)
END = date(2026, 11, 18)  # 네이버 판정 마지막 날 (한국 어제)
FIXTURE = Path(__file__).parent / "fixtures" / "trend_report_example.json"


# ---------------------------------------------------------------- 가짜


class FakeLLM:
    """의뢰문 추출(_Extracted)·추천(_Recommendation)에 정해 둔 답. 부른 데이터 블록을 남긴다."""

    def __init__(self, extract: dict[str, Any] | None = None,
                 recommend: dict[str, Any] | None = None,
                 error: Exception | None = None) -> None:  # fmt: skip
        self.extract = extract or {}
        self.recommend = recommend
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append((schema.__name__, dict(untrusted or {})))
        if self.error:
            raise self.error
        if schema.__name__ == "_Extracted":
            return schema.model_validate(self.extract)
        ids = [k.removeprefix("근거_") for k in (untrusted or {}) if k.startswith("근거_")]
        # 11/23: 추천은 콘티(beats) 대신 기획에 넘길 재료(materials). 기본 답은 숫자·시간 말 없이
        words = {"hashtag": "해시태그를 쓴다", "format": "릴스 형식으로", "topic": "리필 주제로",
                 "writing": "첫 줄을 질문으로"}  # fmt: skip
        answer = self.recommend or {
            "conclusion": "질문형 첫 줄로 리필을 보여 주는 릴스",
            "materials": [{"kind": k, "text": t, "evidence": ids[:1]} for k, t in words.items()],
        }  # fmt: skip
        return schema.model_validate(answer)


def _post(account: str, i: int, *, days_ago: float, views: int = 1000, likes: int = 100,
          caption: str = "평범한 글", reels: bool = True) -> dict[str, Any]:  # fmt: skip
    return {"id": f"{account}-{i}", "media_product_type": "REELS" if reels else "FEED",
            "media_type": "VIDEO" if reels else "IMAGE",
            "timestamp": (NOW - timedelta(days=days_ago)).isoformat(),
            "view_count": views if reels else None, "like_count": likes, "comments_count": 2,
            "caption": caption, "permalink": f"https://ig/{account}{i}"}  # fmt: skip


def _account(account: str, tag: str | None = "#클린뷰티", hot: bool = True) -> list[dict[str, Any]]:
    """20개: 하루에 하나씩(0.5일 전부터), 첫 글은 터진 릴스(조회 10000)·해시태그."""
    items = [_post(account, i, days_ago=0.5 + i) for i in range(20)]
    if hot:
        items[0] = _post(account, 0, days_ago=0.5, views=10000, caption=f"써 보셨어요?\n{tag}")
    return items


class FakeGraph:
    def __init__(self, media: dict[str, list[dict[str, Any]]], *, limit: int | None = None,
                 errors: dict[str, IgError] | None = None) -> None:  # fmt: skip
        self.media, self.limit, self.errors = media, limit, errors or {}
        self.configured = True
        self.calls_today = 0
        self.asked: list[str] = []

    async def business_media(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        if self.limit is not None and self.calls_today >= self.limit:
            raise ConnectorUnavailable("RATE_LIMITED", "하루 상한")  # 부르지 않음 (수 안 셈)
        self.calls_today += 1
        self.asked.append(username)
        if username in self.errors:
            raise self.errors[username]
        if username not in self.media:
            raise IgError("NO_DATA", "데이터 없음")
        return self.media[username]


def _points(prior: float, recent: float, end: date = END) -> tuple[tuple[date, float], ...]:
    out = []
    for back in range(35):
        d = end - timedelta(days=back)
        out.append((d, recent if back < 7 else prior))
    return tuple(sorted(out))


class FakeSearch:
    daily_limit = 1000

    def __init__(self, error: ConnectorUnavailable | None = None) -> None:
        self.calls_today = 0
        self.queries: list[TrendScanInput] = []
        self.error = error

    async def search_trend(self, query: TrendScanInput, ages: Any = ()) -> TrendResponse:
        self.queries.append(query)
        self.calls_today += 1
        if self.error:
            raise self.error
        series = tuple(TrendSeries(g.group_name, tuple(g.keywords), _points(10, 12))
                       for g in query.keyword_groups)  # fmt: skip
        return TrendResponse(query.start_date, query.end_date, "date", series)


class FakeShopping:
    daily_limit = 1000

    def __init__(self) -> None:
        self.calls_today = 0
        self.asked: list[tuple[str, str, str | None]] = []

    async def keyword_age(self, cid, keyword, start, end, time_unit="date", gender=None):  # noqa: ANN001, ANN201
        self.calls_today += 1
        self.asked.append((cid, keyword, gender))
        # 20·30대는 10 → 9 (−10%), 40대는 크게 올라도 타깃 밖이라 빼야 한다
        return {"20": list(_points(5, 4.5)), "30": list(_points(5, 4.5)),
                "40": list(_points(1, 50))}  # fmt: skip


def _env(tmp_path: Path) -> dict[str, str]:
    return {st.RADAR_ENV: str(tmp_path / "ig_radar.db"),
            trend_age.DB_ENV: str(tmp_path / "trend_age.db")}  # fmt: skip


def _deps(tmp_path: Path, llm: FakeLLM, graph: Any, *, memes: Any = None,
          search: Any = None, shopping: Any = None) -> tr.Deps:  # fmt: skip
    return tr.Deps(env=_env(tmp_path), llm=llm, graph=graph,
                   search=search if search is not None else FakeSearch(),
                   shopping=shopping if shopping is not None else FakeShopping(),
                   memes=(lambda: memes))  # fmt: skip


def _report(request: TrendReportInput) -> TrendReport:
    return TrendReport(report_id="tr_x", status="queued", created_at=NOW, caller=request.caller,
                       brief=tb.input_brief(request))  # fmt: skip


MEMES = ["known", {"items": [
    {"name": "#오늘의루틴", "description": "아침 루틴 영상", "ad_material": "ok"},
    {"name": "사건 이름", "description": None, "ad_material": "excluded"},
]}, {}]  # fmt: skip


# ---------------------------------------------------------------- 프리셋·brief


def test_preset_loads_eco_beauty() -> None:
    p = tb.load_preset("eco_beauty")
    assert p is not None
    # v2: 목적은 product에서 떼어 goal로 (화면이 "목표 · 브랜드 인지도" 칩으로)
    assert p.product == "친환경 화장품" and p.goal == "브랜드 인지도"
    assert p.format == "인스타 릴스 30초"
    assert len(p.media_accounts) == 7 and set(p.media_accounts) <= set(p.accounts)
    assert p.gender == "f" and p.ages == ("20", "30")
    assert p.shopping_category == "50000002"
    assert len(p.keywords) == 5 and "클린뷰티" in p.keywords
    assert len(p.competitors) == 7 and len(p.accounts) == 34
    assert tb.load_preset("../etc") is None and tb.load_preset("nope") is None


def test_choose_preset_by_match_words() -> None:
    choices = tb.presets()
    assert tb.choose_preset("비건 스킨케어 신제품 홍보", choices).name == "eco_beauty"  # type: ignore[union-attr]
    assert tb.choose_preset("여름 여행 패키지 홍보", choices) is None


def test_form_values_override_preset_without_llm() -> None:
    llm = FakeLLM()
    request = TrendReportInput(preset="eco_beauty", competitors=["@Foo.Bar"], keywords=["리필"],
                               target_gender="m", target_ages=["40"],
                               accounts=["extra_1"])  # fmt: skip
    got = asyncio.run(tb.resolve(request, llm))
    b = got.brief
    assert llm.calls == []  # 의뢰문이 없으면 LLM을 부르지 않는다
    assert b.competitors == ["foo.bar"] and b.keywords == ["리필"]
    assert (b.target_gender, b.target_ages) == ("m", ["40"])
    assert b.product == "친환경 화장품" and b.goal == "브랜드 인지도"
    assert b.format_parts == ["인스타 릴스", "30초"]
    assert b.sources.model_dump() == {"product": "preset", "target": "input", "format": "preset",
                                      "competitors": "input", "keywords": "input",
                                      "goal": "preset", "theme": None}  # fmt: skip
    assert got.accounts[:2] == ["foo.bar", "extra_1"]  # 경쟁사·추가 계정이 먼저
    assert len(got.accounts) <= tb.MAX_ACCOUNTS
    assert got.llm_calls == 0


def test_request_text_only_extracts_brief_and_marks_sources() -> None:
    text = "비건 선크림 신제품을 20대 여성에게 알리고 싶어요. 경쟁사는 @abib.official. 릴스 15초."
    llm = FakeLLM(extract={"product": "비건 선크림 신제품", "gender": "f", "ages": ["20대"],
                           "format": "릴스 15초", "competitors": ["@Abib.Official", "브랜드 이름"],
                           "keywords": [], "goal": "출시 알리기"})  # fmt: skip
    got = asyncio.run(tb.resolve(TrendReportInput(request_text=text), llm))
    b = got.brief
    assert len(llm.calls) == 1 and llm.calls[0][1] == {"의뢰문": text}  # 데이터 블록으로만
    assert b.preset == "eco_beauty"  # match_words(비건)로 고름
    assert b.product == "비건 선크림 신제품" and b.format == "릴스 15초"
    assert (b.target_gender, b.target_ages) == ("f", ["20"])
    assert b.competitors == ["abib.official"]  # 아이디 모양이 아닌 것은 버림
    assert len(b.keywords) == 5  # 못 뽑은 칸은 프리셋
    assert b.goal == "출시 알리기" and b.format_parts == ["릴스", "15초"]
    assert b.sources.model_dump() == {"product": "request_text", "target": "request_text",
                                      "format": "request_text", "competitors": "request_text",
                                      "keywords": "preset", "goal": "request_text",
                                      "theme": None}  # fmt: skip (주제 없는 의뢰)


def test_extract_failure_falls_back_to_preset_with_reason() -> None:
    llm = FakeLLM(error=LLMDailyCapReached("하루 상한"))
    got = asyncio.run(tb.resolve(TrendReportInput(request_text="친환경 화장품 홍보"), llm))
    b = got.brief
    assert b.preset == "eco_beauty" and b.product == "친환경 화장품"
    sources = b.sources.model_dump()
    assert sources.pop("theme") is None and b.theme is None  # 의뢰문을 못 읽었으니 주제도 없음
    assert set(sources.values()) == {"preset"}
    assert any("의뢰문을 해석하지 못함" in line for line in got.limits)
    assert got.llm_calls == 1


def test_no_matching_preset_uses_request_values_only() -> None:
    llm = FakeLLM(extract={"product": "여행 패키지", "keywords": ["제주 여행"]})
    got = asyncio.run(tb.resolve(TrendReportInput(request_text="제주 여행 패키지"), llm))
    assert got.brief.preset is None and got.preset is None
    assert got.brief.keywords == ["제주 여행"] and got.brief.competitors == []
    assert any("맞는 프리셋이 없어" in line for line in got.limits)


# ---------------------------------------------------------------- 네이버


def test_age_codes() -> None:
    codes, notes = tb.search_age_codes(["20", "30"])
    assert codes == ["3", "4", "5", "6"]
    assert notes and "19세" in notes[0]
    assert tb.search_age_codes(["50"])[0] == ["9", "10"]
    assert tb.shopping_groups(["20", "30"]) == ["20", "30"]


def test_datalab_body_and_cache_key_include_filters() -> None:
    group = [TrendKeywordGroup(group_name="리필", keywords=["리필"])]
    plain = TrendScanInput(keyword_groups=group, start_date=END - timedelta(days=34), end_date=END)
    body = naver_datalab._request_body(plain)
    assert "ages" not in body and "gender" not in body  # 없으면 예전 본문 그대로
    filtered = plain.model_copy(update={"ages": ["3", "4"], "gender": "f"})
    assert naver_datalab._request_body(filtered)["ages"] == ["3", "4"]
    assert naver_datalab._request_body(filtered)["gender"] == "f"
    with pytest.raises(ValueError, match="연령 코드"):
        TrendScanInput(keyword_groups=group, start_date=END, end_date=END, ages=["20"])
    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        data = [{"period": d.isoformat(), "ratio": 10} for d, _ in _points(1, 1)]
        return httpx.Response(200, json={"results": [{"title": "리필", "keywords": ["리필"],
                                                      "data": data}]})  # fmt: skip

    env = {naver_datalab.CLIENT_ID_ENV: "id", naver_datalab.CLIENT_SECRET_ENV: "secret"}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    conn = naver_datalab.NaverDatalabConnector(env, client=client,
                                               cache=naver_datalab.DailyCache())  # fmt: skip
    asyncio.run(conn.search_trend(plain))
    asyncio.run(conn.search_trend(filtered))
    asyncio.run(conn.search_trend(filtered))  # 같은 필터 → 캐시
    asyncio.run(conn.search_trend(filtered.model_copy(update={"gender": "m"})))
    assert [b.get("gender") for b in sent] == [None, "f", "m"]


def test_shopping_keyword_age_sends_gender_only_when_given() -> None:
    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"title": "리필", "keyword": ["리필"],
                                                      "data": []}]})  # fmt: skip

    env = {"NAVER_HUB_CLIENT_ID": "id", "NAVER_HUB_CLIENT_SECRET": "secret"}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    conn = naver_shopping.NaverShoppingConnector(env, client=client,
                                                 cache=naver_shopping.DayCache())  # fmt: skip
    try:
        asyncio.run(conn.keyword_age("50000002", "리필", END - timedelta(days=34), END))
        asyncio.run(conn.keyword_age("50000002", "리필", END - timedelta(days=34), END,
                                     gender="f"))  # fmt: skip
    finally:
        naver_datalab.clear_cache()
    assert "gender" not in sent[0] and sent[1]["gender"] == "f"


def test_change_pct() -> None:
    assert tr.change_pct(_points(10, 12), END) == (20.0, None)
    assert tr.change_pct((), END)[0] is None
    only_recent = tuple(p for p in _points(10, 12) if p[0] > END - timedelta(days=7))
    assert tr.change_pct(only_recent, END) == (None, "최근 7일 또는 직전 28일 값이 비어 있음")


def test_search_section_filters_and_shopping_target_groups(tmp_path: Path) -> None:
    search, shopping = FakeSearch(), FakeShopping()
    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    section, calls, limits = asyncio.run(tr.search_section(
        brief, "50000002", search, shopping, _env(tmp_path), NOW))  # fmt: skip
    assert section.status == "known" and len(section.rows) == 5
    assert search.queries[0].ages == ["3", "4", "5", "6"] and search.queries[0].gender == "f"
    assert len(search.queries[0].keyword_groups) == 5  # 키워드 5개를 한 요청으로
    assert all(r.search_change_pct == 20.0 for r in section.rows)
    assert all(r.shopping_change_pct == -10.0 for r in section.rows)  # 40대(크게 오름)는 뺌
    assert [g for _, _, g in shopping.asked] == ["f"] * 5
    assert calls == 6
    assert "19세" in section.filter_note
    store = trend_age.AgeStore(_env(tmp_path)[trend_age.DB_ENV])
    assert store.calls_by_caller(TODAY).get(tr.CALLER) == 6  # trend_age 예산 표에 같이 셈
    store.close()


def test_search_section_cap_marks_keywords_not_checked(tmp_path: Path) -> None:
    env = _env(tmp_path)
    store = trend_age.AgeStore(env[trend_age.DB_ENV])
    store.add_calls(TODAY, 200, "trend_insight_brief")  # 하루 예산 다 씀
    store.close()
    search, shopping = FakeSearch(), FakeShopping()
    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    section, calls, limits = asyncio.run(tr.search_section(brief, "50000002", search, shopping,
                                                           env, NOW))  # fmt: skip
    assert calls == 0 and search.queries == [] and shopping.asked == []
    assert all(r.search_reason == tr.CAPPED and r.shopping_reason == tr.CAPPED
               for r in section.rows)  # fmt: skip
    assert section.status == "unknown"
    assert any("하루 상한" in line for line in limits)


# ---------------------------------------------------------------- 인스타


def test_hashtag_rows_this_week_and_last_week() -> None:
    media = {a: _account(a) for a in ("a", "b", "c")}
    # 직전 7일(8~13일 전)에 #클린뷰티를 쓴 계정: a·b
    for a in ("a", "b"):
        media[a][9] = _post(a, 9, days_ago=9.5, caption="#클린뷰티 지난주")
    media["d"] = _account("d", tag="#다른태그")
    posts = tr.burst_posts(media, NOW)
    section, _ = tr.hashtag_section(media, posts, NOW, None)
    row = next(r for r in section.rows if r.tag == "#클린뷰티")
    assert (row.accounts, row.previous_accounts, row.burst_posts) == (3, 2, 3)
    assert all(r.tag != "#다른태그" for r in section.rows)  # 1곳만 → 3곳 미만이라 뺌


def test_competitor_metrics() -> None:
    items = _account("toun28_official")
    for i in range(10, 20):  # 절반은 사진
        items[i] = _post("toun28_official", i, days_ago=0.5 + i, reels=False)
    row = tr.competitor_row("toun28_official", items, NOW)
    assert row.posts_per_week == round(20 / 19.5 * 7, 1)
    assert row.reels_share == 0.5
    assert row.reels_view_median == 1000
    assert row.best_post is not None and row.best_post.media_id == "toun28_official-0"
    assert len(row.traits) <= tr.MAX_TRAITS


def test_read_accounts_stops_at_limit_and_marks_rest(tmp_path: Path) -> None:
    media = {a: _account(a) for a in ("a", "b", "c", "d")}
    graph = FakeGraph(media, limit=2)
    store = TrendReportStore()
    got = asyncio.run(tr.read_accounts(["a", "b", "c", "d"], graph, store, _env(tmp_path), NOW))
    assert set(got.media) == {"a", "b"}
    assert got.failed == {"c": tr.IG_CAPPED, "d": tr.IG_CAPPED}
    assert got.calls == 2 and graph.asked == ["a", "b"]  # 멈춘 뒤로는 부르지 않는다
    # 같은 날 다시: 저장된 a·b는 부르지 않는다
    again = FakeGraph(media)
    got2 = asyncio.run(tr.read_accounts(["a", "b", "c"], again, store, _env(tmp_path), NOW))
    assert again.asked == ["c"] and got2.reused == 2


def test_read_accounts_one_failure_does_not_stop(tmp_path: Path) -> None:
    media = {a: _account(a) for a in ("a", "c")}
    graph = FakeGraph(media, errors={"b": IgError("PERMISSION", "권한 필요")})
    got = asyncio.run(tr.read_accounts(["a", "b", "c"], graph, TrendReportStore(),
                                       _env(tmp_path), NOW))  # fmt: skip
    assert set(got.media) == {"a", "c"} and got.failed == {"b": "권한 필요"}


def test_read_accounts_reuses_radar_of_today(tmp_path: Path) -> None:
    env = _env(tmp_path)
    radar = st.RadarStore(env[st.RADAR_ENV])
    radar.save(TODAY, "a", _account("a"))
    radar.complete(TODAY, NOW, 1)
    radar.close()
    graph = FakeGraph({"b": _account("b")})
    got = asyncio.run(tr.read_accounts(["a", "b"], graph, TrendReportStore(), env, NOW))
    assert graph.asked == ["b"] and set(got.media) == {"a", "b"} and got.reused == 1


# ---------------------------------------------------------------- 추천 (LLM)


def _filled(tmp_path: Path) -> TrendReport:
    request = TrendReportInput(preset="eco_beauty")
    media = {a: _account(a) for a in tb.load_preset("eco_beauty").accounts[:5]}  # type: ignore[union-attr]
    llm = FakeLLM()
    report = _report(request)
    asyncio.run(tr.build(report, request, _deps(tmp_path, llm, FakeGraph(media), memes=MEMES),
                         TrendReportStore(), now=NOW))  # fmt: skip
    return report


def test_recommendation_keeps_only_valid_ids_and_digit_free_lines(tmp_path: Path) -> None:
    report = _filled(tmp_path)
    catalog = tr.evidence_catalog(report)
    good = next(k for k in catalog if k.startswith("hashtag:"))
    llm = FakeLLM(recommend={"conclusion": "조회수 3배를 노린다", "materials": [
        {"kind": "hashtag", "text": "해시태그를 앞에 쓴다", "evidence": [f"근거_{good}", "없는:9"]},
        {"kind": "format", "text": "릴스로", "evidence": ["없는:1"]},
        {"kind": "topic", "text": "후기 2개 보여 주기", "evidence": [good]},
        {"kind": "hashtag", "text": "겹친 종류", "evidence": [good]},
        {"kind": "scene", "text": "틀린 종류", "evidence": [good]},
    ]})  # fmt: skip
    section, calls, limits = asyncio.run(tr.recommendation_section(report, llm))
    assert calls == 1
    assert section.conclusion is None  # 숫자 든 결론은 버림
    assert [m.kind for m in section.materials] == ["hashtag"]
    assert section.materials[0].evidence == [catalog[good][0]]  # 근거 문장은 코드가 붙임
    assert section.beats == []  # 새 보고서는 콘티를 만들지 않는다
    assert section.music_note == tr.MUSIC_NOTE
    # 11/23: 결론이 없으면 재료가 있어도 unknown (예전에는 콘티만 있어도 known)
    assert section.status == "unknown"
    assert any("없는 근거 id" in line for line in limits)
    # LLM에는 숫자 없는 말만
    sent = llm.calls[0][1]
    assert all(not any(ch.isdigit() for ch in v) for k, v in sent.items()
               if k.startswith("근거_hashtag") or k.startswith("근거_search"))  # fmt: skip


def test_recommendation_llm_cap_leaves_only_that_section_empty(tmp_path: Path) -> None:
    request = TrendReportInput(preset="eco_beauty")
    media = {a: _account(a) for a in ("toun28_official", "amuse", "sidmool")}
    llm = FakeLLM(error=LLMDailyCapReached("하루 상한"))
    report = _report(request)
    asyncio.run(tr.build(report, request, _deps(tmp_path, llm, FakeGraph(media), memes=MEMES),
                         TrendReportStore(), now=NOW))  # fmt: skip
    rec = report.sections.recommendation
    assert rec.status == "unknown" and rec.reason and rec.conclusion is None
    assert report.summary_line is None and report.summary_reason
    assert report.sections.search.status == "known"
    assert report.sections.hashtags.status == "known"
    assert report.status == "partial"


def test_full_report_uses_at_most_two_llm_calls(tmp_path: Path) -> None:
    request = TrendReportInput(request_text="친환경 화장품 브랜드를 20대 여성에게 알리고 싶어요")
    media = {a: _account(a) for a in tb.load_preset("eco_beauty").accounts}  # type: ignore[union-attr]
    llm = FakeLLM(extract={"product": "친환경 화장품", "gender": "f", "ages": ["20"]})
    report = _report(request)
    graph = FakeGraph(media)
    asyncio.run(tr.build(report, request, _deps(tmp_path, llm, graph, memes=MEMES),
                         TrendReportStore(), now=NOW))  # fmt: skip
    assert report.status == "done", report.model_dump()
    assert len(llm.calls) == 2 and report.calls.llm == 2
    assert report.calls.instagram == 34 and report.calls.naver == 6
    assert report.summary_line and report.sections.recommendation.materials
    assert report.sections.recommendation.beats == []
    content = report.sections.content
    assert len(content.posts) == tr.MAX_POSTS and content.posts[0].standout
    assert [m.name for m in content.memes] == ["#오늘의루틴"]  # 광고 재료 ok만
    assert report.sections.competitors.rows
    assert any("숫자는 전부 코드가 계산" in line for line in report.limits)


def test_sections_fail_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(tr, "hashtag_section", boom)
    request = TrendReportInput(preset="eco_beauty")
    media = {a: _account(a) for a in ("toun28_official", "amuse", "sidmool")}
    report = _report(request)
    asyncio.run(tr.build(report, request, _deps(tmp_path, FakeLLM(), FakeGraph(media)),
                         TrendReportStore(), now=NOW))  # fmt: skip
    assert report.sections.hashtags.status == "failed"
    assert "RuntimeError" in (report.sections.hashtags.reason or "")
    assert report.sections.search.status == "known"
    assert report.sections.competitors.status == "known"
    assert report.status == "partial"


def test_instagram_key_missing_keeps_naver_section(tmp_path: Path) -> None:
    graph = FakeGraph({})
    graph.configured = False
    request = TrendReportInput(preset="eco_beauty")
    report = _report(request)
    asyncio.run(tr.build(report, request, _deps(tmp_path, FakeLLM(), graph), TrendReportStore(),
                         now=NOW))  # fmt: skip
    assert graph.asked == [] and report.calls.instagram == 0
    assert report.sections.search.status == "known"
    for name in ("hashtags", "competitors"):
        section = getattr(report.sections, name)
        assert section.status == "unknown" and "키 등록 후 가능" in (section.reason or "")
    failed = report.sections.competitors.accounts_failed
    assert len(failed) == 7 and "키 등록 후 가능" in failed[0].reason


# ---------------------------------------------------------------- 실행·저장·알림


def _service(tmp_path: Path, published: list[Any], *, publish: Any = None,
             store: TrendReportStore | None = None) -> TrendReportService:  # fmt: skip
    media = {a: _account(a) for a in ("toun28_official", "amuse", "sidmool")}

    def deps() -> tr.Deps:
        return _deps(tmp_path, FakeLLM(), FakeGraph(media), memes=MEMES)

    async def default(envelope: Any) -> None:
        published.append(envelope)

    return TrendReportService(store or TrendReportStore(), deps, publish=publish or default,
                              clock=lambda: NOW)  # fmt: skip


def test_same_request_is_reused_and_rebuild_makes_new(tmp_path: Path) -> None:
    published: list[Any] = []

    async def run() -> tuple[Any, Any, Any]:
        service = _service(tmp_path, published)
        request = TrendReportInput(preset="eco_beauty", keywords=["리필 화장품"], caller="planning")
        first = await service.create(request, "ctx_1")
        await service.wait(first.report_id)
        same = await service.create(request.model_copy(update={"caller": "human"}))
        again = await service.create(request.model_copy(update={"rebuild": True}))
        await service.wait(again.report_id)
        return first, same, again

    first, same, again = asyncio.run(run())
    assert first.status == "queued" and not first.reused and first.eta_seconds
    assert same.reused and same.report_id == first.report_id and same.status == "done"
    assert not again.reused and again.report_id != first.report_id
    assert len(published) == 2


def test_event_body_and_publish_failure_keeps_report(tmp_path: Path) -> None:
    published: list[Any] = []

    async def broken(envelope: Any) -> None:
        from ax_agent_template.events import EventBusUnavailable

        raise EventBusUnavailable("REDIS_URL 없음")

    async def run() -> tuple[TrendReport | None, TrendReport | None]:
        ok = _service(tmp_path, published)
        a = await ok.create(TrendReportInput(preset="eco_beauty", caller="orchestrator",
                                             context_id="ctx_o"))  # fmt: skip
        await ok.wait(a.report_id)
        bad = _service(tmp_path, [], publish=broken)
        b = await bad.create(TrendReportInput(preset="eco_beauty"))
        await bad.wait(b.report_id)
        return ok.get(a.report_id), bad.get(b.report_id)

    good, after_fail = asyncio.run(run())
    assert good is not None and after_fail is not None
    assert after_fail.status == "done"  # 알림을 못 보내도 보고서는 그대로
    env = published[0]
    assert env.type == EventType.TREND_REPORT_READY and env.source == "data"
    assert env.idempotency_key == f"{good.report_id}:done"
    assert env.data == {"report_id": good.report_id, "status": "done", "context_id": "ctx_o",
                        "caller": "orchestrator", "summary_line": good.summary_line}  # fmt: skip
    assert EVENT_DATA_MODELS[EventType.TREND_REPORT_READY] is TrendReportReadyData
    TrendReportReadyData.model_validate(env.data)


def test_one_report_at_a_time_and_progress_saved(tmp_path: Path) -> None:
    published: list[Any] = []
    seen: list[str] = []

    async def run() -> None:
        service = _service(tmp_path, published)
        original = tr.build

        async def watched(report: Any, *args: Any, **kwargs: Any) -> Any:
            seen.append(f"start:{report.report_id}")
            out = await original(report, *args, **kwargs)
            seen.append(f"end:{report.report_id}")
            return out

        tr.build = watched  # type: ignore[assignment]
        try:
            a = await service.create(TrendReportInput(preset="eco_beauty", keywords=["가"]))
            b = await service.create(TrendReportInput(preset="eco_beauty", keywords=["나"]))
            assert service.store.statuses(b.report_id)[0] == "queued"  # type: ignore[index]
            assert b.eta_seconds and a.eta_seconds and b.eta_seconds > a.eta_seconds
            await service.wait(a.report_id)
            await service.wait(b.report_id)
            sections = service.store.statuses(a.report_id)[1]  # type: ignore[index]
            assert sections["search"] == "known"  # 섹션별 상태도 저장
            items = service.list(10)  # 시계를 고정해 만든 시각이 같다: 순서 대신 모두 있는지
            assert {i.report_id for i in items} == {a.report_id, b.report_id}
            assert all(i.status == "done" and i.product for i in items)
        finally:
            tr.build = original  # type: ignore[assignment]

    asyncio.run(run())
    assert seen[0].startswith("start:") and seen[1].startswith("end:")  # 겹치지 않음
    assert seen[0][6:] == seen[1][4:]


def test_restart_marks_live_reports_interrupted() -> None:
    store = TrendReportStore()
    report = TrendReport(report_id="tr_live", status="running", created_at=NOW, caller="human",
                         brief=tb.input_brief(TrendReportInput()))  # fmt: skip
    report.sections.search.status = "known"
    report.sections.hashtags.status = "running"
    store.create("tr_live", "h", TODAY, {}, "human", None, report.model_dump(mode="json"), NOW)
    service = TrendReportService(store, lambda: None, clock=lambda: NOW)  # type: ignore[arg-type,return-value]
    got = service.get("tr_live")
    assert service.interrupted == ["tr_live"]
    assert got is not None and got.status == "interrupted"
    assert got.sections.search.status == "known"
    hashtags = got.sections.hashtags
    assert hashtags.status == "failed" and hashtags.reason == INTERRUPTED_REASON
    assert INTERRUPTED_REASON in got.limits


def test_handlers_create_get_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[Any] = []
    service = _service(tmp_path, published)
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)
    meta = RequestMeta(context_id="ctx_h", token="x",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip

    def call(skill: str, data: dict[str, Any]) -> Any:
        return handlers.HANDLERS[skill](AgentRequest(meta=meta, skill=skill, input=data))

    async def run() -> tuple[Any, Any, Any, Any]:
        created = await call("trend_report_create", {"request_text": "친환경 화장품 홍보"})
        await service.wait(created.output["report_id"])
        got = await call("trend_report_get", {"report_id": created.output["report_id"]})
        listed = await call("trend_report_list", {})
        missing = await call("trend_report_get", {"report_id": "tr_none"})
        return created, got, listed, missing

    created, got, listed, missing = asyncio.run(run())
    assert created.output["status"] == "queued" and created.output["reused"] is False
    report = TrendReport.model_validate(got.output)
    assert report.context_id == "ctx_h" and report.status in ("done", "partial")
    assert listed.output["items"][0]["report_id"] == report.report_id
    assert missing.outcome == "not_found"
    bad = asyncio.run(call("trend_report_create", {"target_gender": "x"}))
    assert bad.outcome == "invalid_input"


# ---------------------------------------------------------------- 계약 예시


def test_contract_example_validates() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = TrendReport.model_validate(raw)
    dumped = report.model_dump(mode="json")
    assert dumped["sections"]["search"]["rows"][0]["keyword"] == "리필 화장품"
    assert dumped == raw  # 예시 그대로 (빠진 칸·바뀐 값 없음)
    post = report.sections.content.posts[0]
    assert isinstance(post, InstagramRadarPost) and post.thumbnail_url and post.standout
    assert {b.slot for b in report.sections.recommendation.beats} <= set(tr.BEAT_SLOTS)


def test_frontend_questions_1380() -> None:
    """프론트 #1380: 밈 ad_material은 trend_memes와 같은 값, request_text·caller만 보내도 된다."""
    import typing

    from ax_contracts.schemas.data import TrendMeme, TrendReportMeme

    ours = typing.get_args(TrendReportMeme.model_fields["ad_material"].annotation)
    theirs = typing.get_args(TrendMeme.model_fields["ad_material"].annotation)
    assert ours == theirs == ("ok", "caution", "excluded", "unknown")
    got = TrendReportInput.model_validate({"request_text": "의뢰", "caller": "orchestrator"})
    assert got.model_dump() == {
        "preset": None, "request_text": "의뢰", "product": None, "target_gender": None,
        "target_ages": None, "format": None, "competitors": None, "keywords": None,
        "accounts": None, "caller": "orchestrator", "context_id": None, "rebuild": False,
        "goal": None,  # v2 추가 (기본 None)
        "chat_thread_id": None,  # 11/19 대화 카드 추가 (기본 None)
        "theme": None, "theme_mode": None,  # 11/20 주제 추가 (기본 None)
    }  # fmt: skip
    assert TrendReportInput.model_validate({}).caller == "human"


def test_ready_event_for_example() -> None:
    report = TrendReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    env = ready_event(report, NOW)
    assert env.type == EventType.TREND_REPORT_READY
    assert env.idempotency_key == "tr_0123456789abcdef:done"
    assert env.context_id == "ctx_trend_1"


def test_llm_not_configured_brief_reason() -> None:
    got = asyncio.run(tb.extract("의뢰", FakeLLM(error=LLMNotConfiguredError("키 없음"))))
    assert got[0] is None and got[1] and "의뢰문을 해석하지 못함" in got[1]


# ---------------------------------------------------------------- v2 (보고서 tr_4913cb6a11274238)


def test_format_parts_keeps_format_when_no_duration() -> None:
    assert tb.format_parts("인스타 릴스 30초") == ["인스타 릴스", "30초"]
    assert tb.format_parts("유튜브 쇼츠") == ["유튜브 쇼츠"]
    assert tb.format_parts("30 초 릴스") == ["릴스", "30초"]
    assert tb.format_parts(None) == []


# A2 해시태그: 표기용 태그·매거진 빼기, used_by·is_new


def test_hashtags_hide_ad_tags_and_magazines() -> None:
    media = {a: _account(a, tag="#광고 #협찬 #AD #클린뷰티") for a in ("a", "b", "c")}
    for m in ("voguekorea", "wkorea", "ellekorea"):
        media[m] = _account(m, tag="#비건뷰티 #유료광고")
    for a in ("a", "b"):  # 직전 7일 #클린뷰티: a·b → 새로 뜸 아님
        media[a][9] = _post(a, 9, days_ago=9.5, caption="#클린뷰티")
    media["a"][1] = _post("a", 1, days_ago=1.5, caption="#클린뷰티 또")
    media["a"][0][st_owner()] = "톤28"
    posts = tr.burst_posts(media, NOW)
    section, _ = tr.hashtag_section(media, posts, NOW, None,
                                    ("voguekorea", "wkorea", "ellekorea"))  # fmt: skip
    tags = [r.tag for r in section.rows]
    assert tags == ["#클린뷰티"]  # #광고·#협찬·#ad 없음, 매거진만 쓴 #비건뷰티 없음
    row = section.rows[0]
    assert row.used_by[0] == "a" and set(row.used_by) == {"a", "b", "c"}  # a가 두 번 써서 앞
    assert row.is_new is False and section.account_names == {"a": "톤28"}
    assert all(tr.hidden_tag(f"hashtag:{t}") for t in tr.HIDDEN_TAGS)


def test_hashtags_new_tag_and_unknown_when_only_hidden() -> None:
    media = {a: _account(a, tag="#리필스테이션") for a in ("a", "b", "c")}
    section, _ = tr.hashtag_section(media, tr.burst_posts(media, NOW), NOW, None)
    assert section.rows[0].is_new is True and section.rows[0].previous_accounts == 0
    only_ads = {a: _account(a, tag="#광고 #이벤트 #giveaway") for a in ("a", "b", "c")}
    section, _ = tr.hashtag_section(only_ads, [], NOW, None)
    # 11/20: 3곳을 읽었는데 같이 쓴 태그가 없음 = 봤지만 없음(empty), 이유에 읽은 계정 수
    assert section.status == "empty" and section.reason == f"읽은 계정 3곳이 {tr.NO_SHARED_TAG}"
    assert section.rows == [] and "뷰티" not in (section.reason or "")


def st_owner() -> str:
    from data_agent.connectors.instagram_graph import OWNER_NAME

    return OWNER_NAME


# A3 기준값 작음


def test_small_base_rule() -> None:
    assert tr.small_base(_points(1.5, 30), END) is True  # 직전 28일 평균 < 2.0
    assert tr.small_base(_points(10, 12), END) is False
    # 35일 중 18일이 0 (직전 28일 평균은 2.5라 평균 규칙에는 안 걸림)
    zeros = tuple((d, 0.0 if i % 2 == 0 else 5.0) for i, (d, _) in enumerate(_points(5, 5)))
    assert tr.small_base(zeros, END) is True  # 0인 날이 절반 이상
    fewer = tuple((d, 0.0 if i % 2 else 5.0) for i, (d, _) in enumerate(_points(5, 5)))
    assert tr.small_base(fewer, END) is False  # 17일 → 절반 미만


class GappySearch(FakeSearch):
    """필터 요청이면 `no_filtered` 키워드, 필터 없는 요청이면 `no_all` 키워드에 값이 없다."""

    def __init__(self, no_filtered: set[str], no_all: set[str], small: set[str] = frozenset()) -> None:  # noqa: E501
        super().__init__()
        self.no_filtered, self.no_all, self.small = no_filtered, no_all, small

    async def search_trend(self, query: TrendScanInput, ages: Any = ()) -> TrendResponse:
        self.queries.append(query)
        self.calls_today += 1
        empty = self.no_filtered if (query.ages or query.gender) else self.no_all
        def pts(name: str) -> Any:
            if name in empty:
                return ()
            return _points(0.5, 9) if name in self.small else _points(10, 12)

        series = tuple(TrendSeries(g.group_name, tuple(g.keywords), pts(g.group_name))
                       for g in query.keyword_groups)  # fmt: skip
        return TrendResponse(query.start_date, query.end_date, "date", series)


class FakeSearchAd:
    configured = True

    def __init__(self, volumes: dict[str, tuple[Any, Any]]) -> None:
        self.volumes, self.calls_today, self.hints = volumes, 0, []

    async def keywords(self, hints: list[str]) -> list[Any]:
        from data_agent.connectors.naver_searchad import KeywordStat

        self.calls_today += 1
        self.hints.append(list(hints))
        out = []
        for kw, (pc, mo) in self.volumes.items():
            out.append(KeywordStat(kw, None if pc == "<10" else pc, mo, pc == "<10", False, None))
        return out


# D9 검색 단계별 대체 + A3 small_base 근거 제외


def test_search_falls_back_all_then_volume_then_none(tmp_path: Path) -> None:
    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    # 리필 화장품: 필터 값 있음 / 비건 선크림: 전체로 / 클린뷰티: 기준값 작음 /
    # 제로웨이스트: 월 검색량만 / 비건 화장품: 아무것도 없음
    search = GappySearch(no_filtered={"비건 선크림", "제로웨이스트", "비건 화장품"},
                         no_all={"제로웨이스트", "비건 화장품"}, small={"클린뷰티"})  # fmt: skip
    searchad = FakeSearchAd({"리필화장품": (300, 1200), "제로웨이스트": ("<10", 900)})
    section, calls, limits = asyncio.run(tr.search_section(
        brief, "50000002", search, FakeShopping(), _env(tmp_path), NOW,
        searchad=searchad))  # fmt: skip
    rows = {r.keyword: r for r in section.rows}
    assert rows["리필 화장품"].basis == "filtered" and rows["리필 화장품"].monthly_volume == 1500
    assert rows["비건 선크림"].basis == "all" and rows["비건 선크림"].search_change_pct == 20.0
    assert "거르면 값이 없음 → 전체 기준" in (rows["비건 선크림"].search_reason or "")
    assert rows["클린뷰티"].basis == "filtered" and rows["클린뷰티"].small_base is True
    zero = rows["제로웨이스트"]
    assert zero.basis == "volume_only" and zero.monthly_volume == 900
    assert rows["비건 화장품"].basis == "none" and rows["비건 화장품"].monthly_volume is None
    # 두 번째 요청은 필터 없이 값이 없던 3개만, 검색광고는 띄어쓰기 없는 힌트로 1번
    assert len(search.queries) == 2 and search.queries[1].ages is None
    assert len(search.queries[1].keyword_groups) == 3
    assert searchad.hints == [["리필화장품", "비건선크림", "클린뷰티", "제로웨이스트",
                               "비건화장품"]]  # fmt: skip
    assert calls == 2 + 5 + 1  # 검색어 트렌드 2 · 쇼핑 5 · 검색광고 1
    # AI 추천 근거에는 basis filtered이고 small_base 아닌 검색 행만
    report = _report(TrendReportInput(preset="eco_beauty"))
    report.brief, report.sections.search = brief, section
    ids = [k for k in tr.evidence_catalog(report) if k.startswith("search:")]
    assert ids == ["search:1"]
    notes = tr.search_notes(report)
    assert any("클린뷰티" in n and "기준값이 작아" in n for n in notes)
    assert any("비건 선크림" in n and "전체 기준" in n for n in notes)
    assert any("제로웨이스트" in n and "월 검색량만" in n for n in notes)


def test_monthly_volume_reuses_keyword_pool_of_today(tmp_path: Path) -> None:
    from data_agent import keyword_pool

    path = tmp_path / "pool.db"
    pool = keyword_pool.PoolStore(path)
    pool.save(TODAY, [keyword_pool.PoolRow("리필화장품", 100, 200, False, False, "low",
                                           "related", None)], "ok", None, 1, 1, NOW)  # fmt: skip
    pool.close()
    searchad = FakeSearchAd({"비건선크림": (10, 20)})
    got, calls = asyncio.run(tr.monthly_volumes(["리필 화장품", "비건 선크림"], searchad, path,
                                                TODAY))  # fmt: skip
    assert got == {"리필 화장품": 300, "비건 선크림": 30}
    assert searchad.hints == [["비건선크림"]] and calls == 1  # 풀에 있는 건 다시 묻지 않음
    # 어제 풀이면 쓰지 않는다
    got, _ = asyncio.run(tr.monthly_volumes(["리필 화장품"], None, path,
                                            TODAY + timedelta(days=1)))  # fmt: skip
    assert got == {}


# A4 쇼핑인사이트: 창 끝 = 값이 있는 마지막 날, 빈 이유 셋으로


def test_shopping_window_ends_at_last_value_day() -> None:
    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    late = END - timedelta(days=3)  # 사흘 늦게 채워짐
    pts = [(d, v) for d, v in _points(5, 4.5, end=late)] + [(END, 0.0)]
    pct, why, last = tr.shopping_change({"20": pts, "30": pts}, ["20", "30"], brief)
    assert (pct, why, last) == (-10.0, None, late)


def test_shopping_empty_reasons_are_separated() -> None:
    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    pct, why, _ = tr.shopping_change({}, ["20", "30"], brief)
    assert pct is None and why and why.startswith("그 분야에 없는 키워드")
    pct, why, _ = tr.shopping_change({"40": list(_points(5, 5))}, ["20", "30"], brief)
    assert pct is None and why and why.startswith("값이 너무 적음") and "여성" in why
    few = [(END - timedelta(days=1), 3.0), (END, 2.0)]
    pct, why, _ = tr.shopping_change({"20": few}, ["20", "30"], brief)
    assert pct is None and why and "값이 있는 날 2일" in why


def test_shopping_request_error_reason_keeps_http_code(tmp_path: Path) -> None:
    class Broken(FakeShopping):
        async def keyword_age(self, *args: Any, **kwargs: Any) -> Any:
            self.calls_today += 1
            raise ConnectorUnavailable("BAD_REQUEST", "네이버 쇼핑인사이트 응답 400 (BAD_REQUEST)")

    brief = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM())).brief
    section, _, _ = asyncio.run(tr.search_section(brief, "50000002", FakeSearch(), Broken(),
                                                  _env(tmp_path), NOW))  # fmt: skip
    why = section.rows[0].shopping_reason or ""
    assert why.startswith("요청 오류 [BAD_REQUEST]") and "400" in why


def test_shopping_request_body_and_doc_example_parse() -> None:
    """공식 문서(naver-api-hub-shopping-insight-keyword-age.md)의 본문 필드·응답 예시."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "startDate": "2023-11-01", "endDate": "2023-11-07", "timeUnit": "date",
            "results": [{"title": "정장", "keyword": ["정장"], "data": [
                {"period": "2023-11-01", "ratio": 18.18181, "group": "20"},
                {"period": "2023-11-01", "ratio": 18.18181, "group": "30"},
                {"period": "2023-11-01", "ratio": 36.36363, "group": "40"},
                {"period": "2023-11-01", "ratio": 9.0909, "group": "50"}]}]})  # fmt: skip

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    conn = naver_shopping.NaverShoppingConnector(
        {"NAVER_HUB_CLIENT_ID": "i", "NAVER_HUB_CLIENT_SECRET": "s"}, client=client,
        cache=naver_shopping.DayCache())  # fmt: skip
    got = asyncio.run(conn.keyword_age("50000000", "정장", date(2023, 11, 1), date(2023, 11, 7),
                                       gender="f"))  # fmt: skip
    assert sent == [{"startDate": "2023-11-01", "endDate": "2023-11-07", "timeUnit": "date",
                     "category": "50000000", "keyword": "정장", "gender": "f"}]  # fmt: skip
    assert got["20"] == [(date(2023, 11, 1), 18.18181)] and set(got) == {"20", "30", "40", "50"}


# F13 경쟁사 표시 이름·traits_structured, 근거 short


def test_competitor_name_and_structured_traits() -> None:
    items = _account("toun28_official")
    for i in range(10, 20):
        items[i] = _post("toun28_official", i, days_ago=0.5 + i, reels=False)
    for i in range(5):  # 잘 된 것(릴스 조회 상위)은 질문으로 시작
        items[i] = _post("toun28_official", i, days_ago=0.5 + i, views=5000 + i,
                         caption="써 보셨어요?\n긴 캡션 " * 5)  # fmt: skip
    for m in items:
        m[st_owner()] = "톤28"
    row = tr.competitor_row("toun28_official", items, NOW)
    assert row.name == "톤28"
    labels = {t.label for t in row.traits_structured}
    assert labels and labels <= {"릴스 비율", "첫 줄 질문", "올린 시간", "캡션 길이"}
    assert all(t.baseline.startswith("평소") or ", 평소" in t.baseline
               for t in row.traits_structured)  # fmt: skip
    nameless = tr.competitor_row("x", _account("x"), NOW)
    assert nameless.name is None


def test_evidence_short_and_items_and_limit_groups(tmp_path: Path) -> None:
    report = _filled(tmp_path)
    catalog = tr.evidence_catalog(report)
    assert catalog and all(len(short) <= tr.SHORT_MAX for _, _, short in catalog.values())
    assert tr.short_of("가" * 40) == "가" * 29 + "…"
    materials = report.sections.recommendation.materials  # 11/23: 콘티 대신 재료
    assert materials and all([e.text for e in m.evidence_items] == m.evidence for m in materials)
    assert all(e.id in catalog for m in materials for e in m.evidence_items)
    groups = report.limit_groups
    assert groups.method == list(tr.METHOD_LIMITS)
    assert groups.cannot_see == list(tr.CANNOT_SEE_LIMITS)
    assert groups.this_report and not set(groups.this_report) & set(tr.FIXED_LIMITS)
    assert set(groups.this_report) <= set(report.limits)
    assert any("19세" in line for line in groups.this_report)
    assert report.brief.competitor_names.keys() == set(report.brief.competitors)
