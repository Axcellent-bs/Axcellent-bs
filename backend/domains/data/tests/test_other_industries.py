"""다른 업종 검증: 사람 입력 없이 도메인 프로필 → 출처 목록 → 수집 계획이 업종마다 다르게 나오는지.

- 가짜 테넌트 2개(패션 쇼핑몰, 식품)는 **이 테스트 안의 카탈로그로만** 만든다.
  실제 고객사 설정은 없다
- 회사 설정에는 회사 소개만 있고 덮어쓰기·키워드·카테고리·출처는 적지 않는다
- LLM은 가짜다: 데이터 블록(소개·카탈로그)만 보고 답하는 규칙 함수.
  진짜 LLM 대신 "같은 입력이면 같은 답"을 주려는 것이고,
  검증은 코드(근거 대조·목록 대조·업종 대조·할당량)가 한다
- YouTube 카테고리 목록은 저장된 응답(replay 모드, `DATA_REPLAY_DIR`)으로 실제 커넥터를 거친다
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import DomainProfileRequest, SourcePlanRequest
from data_agent.connectors import CatalogProduct, replay
from data_agent.connectors.youtube import DEFAULT_URL, YouTubeConnector
from data_agent.skills import data_sources, domain_profile, domain_subtopics, source_plan
from data_agent.skills.product_link import CatalogRead

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)

# 테스트 전용 가짜 테넌트 (실제 회사 아님)
FASHION = (
    "린넨 셔츠와 와이드 슬랙스를 파는 여성 의류 쇼핑몰이다.",
    CatalogRead(
        products=[
            CatalogProduct(product_id="f1", name="린넨 셔츠", category_code="top"),
            CatalogProduct(product_id="f2", name="와이드 슬랙스", category_code="bottom"),
            CatalogProduct(product_id="f3", name="캔버스 에코백", category_code="bag",
                           description="데일리 에코백"),
        ],
        categories={"top": "상의", "bottom": "하의", "bag": "가방"},
    ),
)  # fmt: skip
FOOD = (
    "제주 감귤과 수제 그래놀라를 파는 식품 쇼핑몰이다.",
    CatalogRead(
        products=[
            CatalogProduct(product_id="g1", name="제주 감귤 5kg", category_code="fruit"),
            CatalogProduct(product_id="g2", name="수제 그래놀라", category_code="cereal",
                           description="귀리 그래놀라"),
        ],
        categories={"fruit": "과일", "cereal": "시리얼"},
    ),
)  # fmt: skip

# YouTube videoCategories.list 저장 응답 (공식 문서 형식으로 만든 테스트 전용)
YT_CATEGORIES = {
    "items": [
        {"id": "26", "snippet": {"title": "노하우/스타일", "assignable": True}},
        {"id": "22", "snippet": {"title": "인물/블로그", "assignable": True}},
        {"id": "27", "snippet": {"title": "교육", "assignable": True}},
    ]
}
INDUSTRY_WORDS = {"패션": ("셔츠", "슬랙스", "의류"), "식품": ("감귤", "그래놀라", "식품")}
SHOPPING_CAT = {"패션": "50000000", "식품": "50000006"}


class RuleLLM(MockLLMClient):
    """가짜 LLM: 데이터 블록만 보고 답한다 (사람 입력·업종별 하드코딩 설정 없음)."""

    def __init__(self) -> None:
        super().__init__()
        self.profile_seen = ""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        data = dict(untrusted or {})
        if schema is domain_profile._Profile:
            text = json.dumps(data, ensure_ascii=False)
            self.profile_seen = text
            industry = next(
                (k for k, words in INDUSTRY_WORDS.items() if any(w in text for w in words)), "기타"
            )
            names = [v.split(" | ")[0] for k, v in data.items() if k.startswith("상품")]
            words = {n: [w for w in n.split() if not any(c.isdigit() for c in w)] for n in names}
            seeds = [domain_profile._Seed(keyword=words[n][-1], evidence=n) for n in names]
            seeds.append(
                domain_profile._Seed(keyword="여름 원피스", evidence="여름 원피스")
            )  # 근거 없음
            return domain_profile._Profile(
                industry=industry, customer_axes=["연령대", "성별"], seeds=seeds,
                questions=["어떤 연령대가 이 상품군을 찾나"],
            )  # fmt: skip
        if schema is source_plan._Plan:
            profile = json.loads(data["회사 프로필"])
            seeds = profile["씨앗 키워드"][:3]
            cat = SHOPPING_CAT.get(profile["업종"], "50000000")
            return source_plan._Plan(items=[
                source_plan._Pick(source_id="naver_search_trend", reason="검색 관심",
                                  keywords=seeds, use_age=True),
                source_plan._Pick(source_id="naver_shopping", reason="쇼핑 클릭",
                                  category_ids=[cat], keywords=seeds[:1], use_age=True),
                source_plan._Pick(source_id="youtube", reason="숏츠 반응", category_ids=["26"],
                                  keywords=seeds[:1]),
                source_plan._Pick(source_id="data4library", reason="대출", category_ids=["5"]),
                source_plan._Pick(source_id="kosis", reason="조사 통계"),
            ])  # fmt: skip
        raise AssertionError(f"예상 못 한 질문 {schema}")


def _tenant(tmp_path: Path, about: str) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "tenant.toml"
    cfg.write_text(f'name = "테스트"\n[profile]\nabout = "{about}"\n', encoding="utf-8")
    replay_dir = tmp_path / "replay"
    params = {"part": "snippet", "regionCode": "KR", "hl": "ko"}
    env = {
        "DATA_TENANT_CONFIG": str(cfg), "DATA_DOMAIN_PROFILE_DB": str(tmp_path / "p.db"),
        "DATA_SOURCE_PROBE_DB": str(tmp_path / "probe.db"),
        "DATA_SOURCE_PLAN_DB": str(tmp_path / "plan.db"), "DATA_REPLAY_DIR": str(replay_dir),
        "DATA_EXTERNAL_MODE": "replay", "YOUTUBE_API_KEY": "k", "NAVER_HUB_CLIENT_ID": "i",
        "NAVER_HUB_CLIENT_SECRET": "s",
    }  # fmt: skip
    replay.save("youtube", f"{DEFAULT_URL}/videoCategories", params, YT_CATEGORIES, env)
    return env


async def _probe_ok(source_id: str) -> tuple[bool, str]:
    return True, "쇼핑인사이트 호출 성공"


async def _verify(cids):  # noqa: ANN001, ANN202
    return list(cids), []


def _pipeline(tmp_path: Path, tenant: tuple[str, CatalogRead]):  # noqa: ANN202
    about, catalog = tenant
    env = _tenant(tmp_path, about)
    llm = RuleLLM()

    async def go():  # noqa: ANN202
        profile = await domain_profile.make_profile(
            DomainProfileRequest(), llm, env=env, catalog=catalog, now=NOW
        )
        sources = await data_sources.list_sources(env, prober=_probe_ok, now=NOW)
        plan = await source_plan.make_plan(
            SourcePlanRequest(), llm, env=env, profile=profile.output, sources=sources.output,
            youtube_categories=YouTubeConnector(env).video_categories, verify_shopping=_verify,
            now=NOW,
        )  # fmt: skip
        return profile.output, sources.output, plan.output

    return asyncio.run(go())


def test_fashion_and_food_get_different_profiles_without_human_input(tmp_path: Path) -> None:
    fashion, _, _ = _pipeline(tmp_path / "fashion", FASHION)
    food, _, _ = _pipeline(tmp_path / "food", FOOD)
    assert fashion.industry == "패션" and food.industry == "식품"
    assert fashion.overridden == [] and food.overridden == []  # 사람 덮어쓰기 없음
    assert {s.keyword for s in fashion.seed_keywords} == {"셔츠", "슬랙스", "에코백"}
    assert {s.keyword for s in food.seed_keywords} == {"감귤", "그래놀라"}
    # 근거 없는 키워드는 업종과 상관없이 코드가 버린다
    assert "여름 원피스" in {d.keyword for d in fashion.dropped_keywords}
    assert "캣북스" not in fashion.model_dump_json() + food.model_dump_json()


def test_plans_pick_different_categories_and_drop_mismatched_sources(tmp_path: Path) -> None:
    _, _, fashion = _pipeline(tmp_path / "fashion", FASHION)
    _, _, food = _pipeline(tmp_path / "food", FOOD)
    shop = {p.plan_id: next(i for i in p.items if i.source_id == "naver_shopping")
            for p in (fashion, food)}  # fmt: skip
    assert [c.category_id for c in shop[fashion.plan_id].categories] == ["50000000"]
    assert [c.category_id for c in shop[food.plan_id].categories] == ["50000006"]
    assert shop[food.plan_id].categories[0].name == "식품"
    for plan in (fashion, food):  # 출판·교육 전용 출처는 업종이 달라 코드가 버린다
        assert "data4library" not in {i.source_id for i in plan.items}
        assert any(d.value == "data4library" for d in plan.dropped_sources)
        yt = next(i for i in plan.items if i.source_id == "youtube")
        assert yt.category_check == "verified" and yt.categories[0].name == "노하우/스타일"
        kosis = next(i for i in plan.items if i.source_id == "kosis")
        assert kosis.status == "key_required" and kosis.category_check == "after_key"
    assert {k for i in fashion.items for k in i.keywords} <= {"셔츠", "슬랙스", "에코백"}
    assert {k for i in food.items for k in i.keywords} <= {"감귤", "그래놀라"}
    assert fashion.plan_id != food.plan_id


# ------------------------------------------------ 하위 주제 넓히기 (업종마다 다른 하위 주제)


def _save_shorts(
    env: dict[str, str], seed: str, videos: list[tuple[str, str, list[str], int]]
) -> None:
    """씨앗 하나의 숏츠 검색·영상 통계 응답을 저장해 둔다 (공식 문서 형식, 테스트 전용)."""
    from datetime import timedelta

    from data_agent.skills.domain_subtopics import load_config as sub_config

    cfg = sub_config(env)
    search = {
        "part": "snippet", "q": seed, "type": "video", "videoDuration": "short",
        "regionCode": "KR", "order": "viewCount", "maxResults": cfg.max_results,
        "publishedAfter": (NOW - timedelta(days=cfg.published_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevanceLanguage": "ko",
    }  # fmt: skip
    replay.save("youtube", f"{DEFAULT_URL}/search",  search,
                {"items": [{"id": {"videoId": v[0]}} for v in videos]}, env)  # fmt: skip
    stats = {"part": "snippet,statistics,contentDetails", "id": ",".join(v[0] for v in videos),
             "maxResults": 50}  # fmt: skip
    items = [
        {"id": vid, "snippet": {"publishedAt": "2026-10-30T00:00:00Z", "channelId": "c",
                                "title": title, "tags": tags, "channelTitle": "채널"},
         "statistics": {"viewCount": str(views)}, "contentDetails": {"duration": "PT40S"}}
        for vid, title, tags, views in videos
    ]  # fmt: skip
    replay.save("youtube", f"{DEFAULT_URL}/videos", stats, {"items": items}, env)


class AxisRule(MockLLMClient):
    """가짜 LLM: 축 목록의 첫 축으로 (축 이름은 프로필에서 온다)."""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        data = dict(untrusted or {})
        axes = json.loads(data["축 목록"])
        return domain_subtopics._Axes(items=[domain_subtopics._Axis(id=k[2:], axis=axes[0])
                                             for k in data if k.startswith("주제")])  # fmt: skip


def test_subtopics_differ_by_industry_without_human_input(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import DomainSubtopicsRequest

    results = {}
    for name, tenant, seed, videos, axes in (
        ("fashion", FASHION, "셔츠", [("s1", "린넨 셔츠 코디", ["코디", "출근룩"], 900),
                                      ("s2", "여름 셔츠 코디", ["코디", "출근룩"], 1500)],
         ["스타일", "입는 상황"]),
        ("food", FOOD, "그래놀라", [("g1", "그래놀라 아침 레시피", ["레시피", "다이어트"], 700),
                                    ("g2", "그래놀라 요거트 레시피", ["레시피", "다이어트"], 300)],
         ["먹는 방식", "건강 관심"]),
    ):  # fmt: skip
        profile, _, _ = _pipeline(tmp_path / name, tenant)
        env = _tenant(tmp_path / name, tenant[0]) | {
            "DATA_SUBTOPICS_DB": str(tmp_path / name / "s.db")
        }
        _save_shorts(env, seed, videos)
        prof = profile.model_copy(
            update={
                "subtopic_axes": axes,
                "seed_keywords": [s for s in profile.seed_keywords if s.keyword == seed],
            }
        )  # 축은 프로필 LLM이 정한 값이라 여기서는 업종마다 다른 값을 준다
        out = asyncio.run(
            domain_subtopics.make_subtopics(
                DomainSubtopicsRequest(), AxisRule(), env=env, profile=prof,
                youtube=YouTubeConnector(env), now=NOW,
            )
        ).output  # fmt: skip
        results[name] = out
    fashion, food = results["fashion"], results["food"]
    assert {s.name for s in fashion.subtopics} == {"코디", "출근룩"}
    assert {s.name for s in food.subtopics} == {"레시피", "다이어트"}
    assert {s.axis for s in fashion.subtopics} == {"스타일"}
    assert {s.axis for s in food.subtopics} == {"먹는 방식"}
    assert fashion.searches[0].status == "searched" and fashion.quota_used == 0  # 저장된 응답
