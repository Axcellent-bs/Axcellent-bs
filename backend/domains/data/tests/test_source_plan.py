"""source_plan: 프로필 + 출처 목록 → LLM이 고르고 코드가 검증.

실제 LLM·YouTube·네이버를 부르지 않는다 (가짜 LLM, 가짜 카테고리 목록·확인 함수).
- 목록 밖·중복·업종이 안 맞는 출처는 버린다
- 카테고리는 실제 목록 안에서만, 쇼핑은 확인 호출에서 데이터가 오는 것만
- 키 필요 출처는 카테고리 확인을 건너뛰고(after_key) 키가 있으면 얻는 근거를 남긴다
- 키워드는 씨앗 키워드 안에서만, 할당량 상한 안으로 자른다, 저장 재사용
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import (
    DataSource,
    DataSourcesOutput,
    DomainProfileOutput,
    SeedKeyword,
    SourcePlanRequest,
)
from data_agent.skills import source_plan as sp

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)


def _src(sid: str, status: str = "available", industries=("all",), age=False, **kw) -> DataSource:  # noqa: ANN001, ANN003
    if status == "key_required" and "status_reason" not in kw:
        kw["status_reason"] = "키 필요: " + ", ".join(kw.get("key_envs", []))
    return DataSource(source_id=sid, name=sid, status=status, industries=list(industries),
                      age=age, approved=sid in ("youtube", "naver_search_trend"), **kw)  # fmt: skip


SOURCES = DataSourcesOutput(
    sources=[
        _src("youtube", kinds=["viewing"], key_envs=["YOUTUBE_API_KEY"]),
        _src("google_trending"),
        _src("naver_search_trend", age=True),
        _src("naver_shopping", age=True, kinds=["consumption"]),
        _src("data4library", "key_required", ("출판·교육",), age=True,
             key_envs=["DATA4LIBRARY_API_KEY"]),
        _src("kosis", "key_required", key_envs=["KOSIS_API_KEY"]),
    ]
)  # fmt: skip


def _profile(
    industry: str = "출판·교육", seeds=("고양이 그림책", "무지 노트")
) -> DomainProfileOutput:  # noqa: ANN001
    return DomainProfileOutput(
        status="known", industry=industry, industry_tags=[industry], catalog_hash=f"h-{industry}",
        seed_keywords=[SeedKeyword(keyword=k, evidence=k, evidence_from="product") for k in seeds],
    )  # fmt: skip


class PlanLLM(MockLLMClient):
    def __init__(self, items: list[sp._Pick]) -> None:
        super().__init__()
        self.items = items
        self.asked = 0
        self.seen: dict[str, str] = {}

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        self.seen = dict(untrusted or {})
        return sp._Plan(items=self.items)


async def _yt() -> dict[str, str]:
    return {"22": "인물/블로그", "27": "교육", "26": "노하우/스타일"}


async def _verify(cids):  # noqa: ANN001, ANN202
    ok = [(n, c) for n, c in cids if c != "50000003"]
    bad = [(n, c, "데이터 없음") for n, c in cids if c == "50000003"]
    return ok, bad


PICKS = [
    sp._Pick(source_id="youtube", reason="교육 숏츠 반응", category_ids=["27", "99"],
             keywords=["고양이 그림책", "캠핑"]),
    sp._Pick(source_id="naver_search_trend", reason="검색 관심", keywords=["무지 노트"],
             use_age=True),
    sp._Pick(source_id="naver_shopping", reason="쇼핑 클릭", category_ids=["50005542", "50000003"],
             keywords=["무지 노트"], use_age=True),
    sp._Pick(source_id="data4library", reason="대출", category_ids=["8", "X"], use_age=True),
    sp._Pick(source_id="google_trending", reason="지금 인기", keywords=["무지 노트"],
             use_age=True),
    sp._Pick(source_id="youtube", reason="중복"),
    sp._Pick(source_id="instagram", reason="목록 밖"),
]  # fmt: skip


def _run(tmp_path: Path, llm: PlanLLM, *, profile=None, sources=SOURCES, extra: str = "",  # noqa: ANN001
         regenerate: bool = False):  # noqa: ANN202  # fmt: skip
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[plan]\n" + extra, encoding="utf-8")
    env = {"DATA_TREND_CONFIG": str(cfg), "DATA_SOURCE_PLAN_DB": str(tmp_path / "plan.db")}
    return asyncio.run(
        sp.make_plan(
            SourcePlanRequest(regenerate=regenerate),
            llm,
            env=env,
            profile=profile or _profile(),
            sources=sources,
            youtube_categories=_yt,
            verify_shopping=_verify,
            now=NOW,
        )  # fmt: skip
    ).output


def test_plan_keeps_only_listed_sources_and_real_categories(tmp_path: Path) -> None:
    out = _run(tmp_path, PlanLLM(PICKS))
    by = {i.source_id: i for i in out.items}
    assert set(by) == {"youtube", "naver_search_trend", "naver_shopping", "data4library",
                       "google_trending"}  # fmt: skip
    dropped = {d.value: d.reason for d in out.dropped_sources}
    assert dropped["instagram"] == "출처 목록에 없는 id" and "youtube" in dropped
    yt = by["youtube"]
    assert [c.category_id for c in yt.categories] == ["27"] and yt.category_check == "verified"
    assert yt.categories[0].name == "교육"
    assert {d.value for d in yt.dropped} == {"99", "캠핑"}  # 실제 목록 밖·씨앗 키워드 밖
    shop = by["naver_shopping"]
    assert [c.category_id for c in shop.categories] == ["50005542"]
    assert any(d.value == "50000003" and "데이터 없음" in d.reason for d in shop.dropped)


def test_key_required_sources_skip_category_check_and_say_what_the_key_unlocks(
    tmp_path: Path,
) -> None:
    out = _run(tmp_path, PlanLLM(PICKS))
    lib = next(i for i in out.items if i.source_id == "data4library")
    assert lib.status == "key_required" and lib.category_check == "after_key"
    assert [c.category_id for c in lib.categories] == ["8"]  # KDC 표 밖(X)은 버림
    assert (lib.unlocks or "").startswith("DATA4LIBRARY_API_KEY가 있으면")
    assert not lib.approved and "승인" in out.approval_note


def test_age_and_keywords_follow_source_capabilities(tmp_path: Path) -> None:
    out = _run(tmp_path, PlanLLM(PICKS))
    by = {i.source_id: i for i in out.items}
    assert by["naver_search_trend"].use_age and by["naver_search_trend"].calls == 5
    google = by["google_trending"]
    assert not google.use_age and google.keywords == []
    assert {d.value for d in google.dropped} == {"use_age", "무지 노트"}


def test_quota_is_counted_by_code_and_trimmed_to_the_cap(tmp_path: Path) -> None:
    out = _run(
        tmp_path, PlanLLM(PICKS), extra="youtube_units = 150\nnaver_search_trend_calls = 3\n"
    )
    by = {i.source_id: i for i in out.items}
    yt = by["youtube"]  # 카테고리 1 + 키워드 1 = 202단위 → 키워드를 잘라 101단위
    assert yt.keywords == [] and yt.quota_units == 101
    trend = by["naver_search_trend"]  # 연령대 5배 = 5번 → 연령대를 꺼서 1번
    assert not trend.use_age and trend.calls == 1
    assert any("youtube: 키워드" in t for t in out.trimmed)
    quota = {q.source_id: q for q in out.quota}
    assert quota["youtube"].cap == 150 and quota["youtube"].unit == "units"


def test_industry_mismatch_drops_the_source(tmp_path: Path) -> None:
    out = _run(tmp_path, PlanLLM(PICKS), profile=_profile("패션", seeds=("린넨 셔츠",)))
    assert "data4library" not in {i.source_id for i in out.items}
    assert any(d.value == "data4library" and "업종이 맞지 않음" in d.reason
               for d in out.dropped_sources)  # fmt: skip


def test_plan_is_stored_and_reused(tmp_path: Path) -> None:
    llm = PlanLLM(PICKS)
    first = _run(tmp_path, llm)
    again = _run(tmp_path, llm)
    assert again.cached and llm.asked == 1 and again.plan_id == first.plan_id
    other = _run(tmp_path, llm, profile=_profile(seeds=("고양이 그림책",)))
    assert not other.cached and other.plan_id != first.plan_id


def test_no_profile_means_no_plan(tmp_path: Path) -> None:
    out = _run(tmp_path, PlanLLM(PICKS),
               profile=DomainProfileOutput(status="unknown", reason="LLM 설정이 없음"))  # fmt: skip
    assert out.status == "unknown" and "도메인 프로필이 없음" in (out.reason or "")


def test_llm_sees_real_category_options(tmp_path: Path) -> None:
    llm = PlanLLM(PICKS)
    _run(tmp_path, llm)
    assert '"27": "교육"' in llm.seen["출처 youtube"]
    assert '"50005542": "도서"' in llm.seen["출처 naver_shopping"]
    assert "키 필요" in llm.seen["출처 kosis"]


def test_youtube_video_categories_reads_assignable_ones() -> None:
    import httpx

    from data_agent.connectors.youtube import YouTubeConnector

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [  # 공식 문서 형식으로 만든 테스트 전용 응답
            {"id": "27", "snippet": {"title": "교육", "assignable": True}},
            {"id": "18", "snippet": {"title": "단편 영화", "assignable": False}},
        ]})  # fmt: skip

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yt = YouTubeConnector({"YOUTUBE_API_KEY": "k"}, client=client)
    assert asyncio.run(yt.video_categories()) == {"27": "교육"}
    assert seen[0].url.path.endswith("/videoCategories")
    assert seen[0].url.params["regionCode"] == "KR" and yt.quota_used == 1


def test_unlock_text_follows_why_the_source_is_unavailable(tmp_path: Path) -> None:
    sources = DataSourcesOutput(sources=[
        _src("naver_shopping", "key_required", age=True,
             status_reason="지금 키로 확인 호출이 실패함 (UNAUTHORIZED: 활성화되어 있지 않음)",
             key_envs=["NAVER_HUB_CLIENT_ID"]),
    ])  # fmt: skip
    picks = [sp._Pick(source_id="naver_shopping", reason="쇼핑", category_ids=["50005542"])]
    out = _run(tmp_path, PlanLLM(picks), sources=sources)
    shop = out.items[0]
    assert (shop.unlocks or "").startswith("이 API를 쓸 수 있게 되면 (지금 키로 확인 호출이 실패함")
    assert shop.category_check == "after_key" and shop.categories[0].category_id == "50005542"


def test_subtopic_searches_join_the_youtube_quota(tmp_path: Path) -> None:
    llm = PlanLLM(PICKS)
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[plan]\nyoutube_units = 400\n", encoding="utf-8")
    env = {"DATA_TREND_CONFIG": str(cfg), "DATA_SOURCE_PLAN_DB": str(tmp_path / "plan.db")}
    out = asyncio.run(
        sp.make_plan(
            SourcePlanRequest(),
            llm,
            env=env,
            profile=_profile(),
            sources=SOURCES,
            youtube_categories=_yt,
            verify_shopping=_verify,
            now=NOW,
            subtopic_pending=3,
        )  # fmt: skip
    ).output
    yt = next(i for i in out.items if i.source_id == "youtube")
    quota = next(q for q in out.quota if q.source_id == "youtube")
    # 계획 항목 202단위(카테고리 1 + 키워드 1)와 하위 주제 검색 3번(303)을 합치면 400을 넘어
    # 항목 키워드를 먼저 자르고(101) 하위 주제 검색은 2번(202)까지
    assert yt.quota_units == 101 and out.subtopic_searches == 2 and out.subtopic_units == 202
    assert quota.units == 303 and quota.units <= quota.cap
    assert any("하위 주제 검색 1번" in t for t in out.trimmed)
    again = asyncio.run(
        sp.make_plan(
            SourcePlanRequest(),
            llm,
            env=env,
            profile=_profile(),
            sources=SOURCES,
            youtube_categories=_yt,
            verify_shopping=_verify,
            now=NOW,
            subtopic_pending=0,
        )  # fmt: skip
    ).output
    assert again.cached and again.subtopic_searches == 0  # 검색 캐시 상태는 그때그때


def test_plan_is_rebuilt_when_the_code_changes(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    llm = PlanLLM(PICKS)
    first = _run(tmp_path, llm)
    assert _run(tmp_path, llm).cached and llm.asked == 1
    monkeypatch.setattr(sp, "CODE_VERSION", "changed")  # 코드가 바뀐 것과 같다 (소스 해시)
    again = _run(tmp_path, llm)
    assert not again.cached and llm.asked == 2 and again.plan_id != first.plan_id


def test_dropped_values_have_a_kind_and_unplanned_sources_say_what_they_unlock(
    tmp_path: Path,
) -> None:
    out = _run(tmp_path, PlanLLM(PICKS))
    assert {d.kind for d in out.dropped_sources} == {"source"}
    by = {i.source_id: i for i in out.items}
    kinds = {d.value: d.kind for d in by["youtube"].dropped}
    assert kinds == {"99": "category", "캠핑": "keyword"}
    assert {d.value: d.kind for d in by["google_trending"].dropped} == {
        "use_age": "age", "무지 노트": "keyword",
    }  # fmt: skip
    assert next(d for d in by["naver_shopping"].dropped if d.value == "50000003").kind == "category"
    rest = {n.source_id: n for n in out.not_planned}
    assert set(rest) == {"kosis"}  # 계획에 든 출처는 없다
    kosis = rest["kosis"]
    assert kosis.status == "key_required" and kosis.reason.startswith("키가 없거나 쓸 수 없음")
    assert kosis.unlocks and "통계" in kosis.unlocks  # 출처 정의의 "등록하면 얻는 근거"
    other = _run(tmp_path / "f", PlanLLM(PICKS), profile=_profile("패션", seeds=("린넨 셔츠",)))
    lib = next(n for n in other.not_planned if n.source_id == "data4library")
    assert lib.reason.startswith("계획에서 뺌: 업종이 맞지 않음")
