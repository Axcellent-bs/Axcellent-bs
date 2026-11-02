"""영상 소재 ↔ 회사 실제 상품 연결 (content_recommend).

실제 shop-api·네이버·LLM·임베딩을 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template.llm import LLMNotConfiguredError, MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import ContentRecommendRequest, TrendScanInput
from data_agent import handlers
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector
from data_agent.explain import PRODUCT_INVALID, _Idea, explain_idea
from data_agent.skills.content_recommend import load_config, recommend
from data_agent.skills.product_link import pick_links, read_catalog, similarities, string_links
from data_agent.skills.trend_scan import REQUEST_DAYS

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)

PRODUCTS = [
    CatalogProduct(product_id="prd_1", name="파이썬 입문서", category_code="book"),
    CatalogProduct(product_id="prd_2", name="에세이 모음집", category_code="book"),
    CatalogProduct(product_id="prd_3", name="젤펜 12색", category_code="stationery"),
    CatalogProduct(product_id="prd_4", name="독서대", category_code="stationery"),
]
CATEGORIES = {"book": "도서", "stationery": "문구"}


# ---------------------------------------------------------------- 글자 일치


def test_name_match_works_both_ways_and_ignores_spaces_and_case() -> None:
    links = string_links(["파이썬"], PRODUCTS, CATEGORIES)
    assert [(x.product.product_id, x.match) for x in links] == [("prd_1", "name")]
    assert links[0].detail == "이름 일치: 파이썬"
    # 상품 이름이 소재 이름 안에 (공백 무시): "독서대 추천" ⊃ 독서대
    assert [x.product.product_id for x in string_links(["독서대 추천"], PRODUCTS, CATEGORIES)] == [
        "prd_4"
    ]


def test_category_name_links_the_products_of_that_category() -> None:
    links = string_links(["도서 추천"], PRODUCTS, CATEGORIES)
    assert [(x.product.product_id, x.match) for x in links] == [
        ("prd_1", "category"),
        ("prd_2", "category"),
    ]


def test_unrelated_topics_and_one_letter_names_link_nothing() -> None:
    assert string_links(["t1", "티원"], PRODUCTS, CATEGORIES) == []
    assert string_links(["책"], PRODUCTS, CATEGORIES) == []  # 한 글자는 너무 넓다


def test_pick_links_orders_name_then_category_then_similarity_and_caps() -> None:
    sims = [0.2, 0.9, 0.7, 0.4]  # prd_1..4
    got = pick_links(
        ["도서 추천", "파이썬"], PRODUCTS, CATEGORIES, sims, min_similarity=0.6, limit=3
    )
    assert [(p.product_id, p.match, p.similarity) for p in got] == [
        ("prd_1", "name", 0.2),
        ("prd_2", "category", 0.9),
        ("prd_3", "embedding", 0.7),
    ]
    assert got[0].category_name == "도서" and got[2].reason == "유사도 0.70"
    none = pick_links(["t1"], PRODUCTS, CATEGORIES, None, min_similarity=0.6, limit=3)
    assert none == []  # 유사도를 못 재면 글자 일치만


# ---------------------------------------------------------------- 상품 목록·유사도


class FakeCatalog:
    def __init__(self, products=PRODUCTS, fail: bool = False) -> None:  # noqa: ANN001
        self._products, self.fail = products, fail

    async def products(self) -> list[CatalogProduct]:
        if self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "shop-api 꺼짐")
        return list(self._products)

    async def categories(self) -> dict[str, str]:
        return dict(CATEGORIES)


def test_read_catalog_reports_why_instead_of_zero() -> None:
    assert asyncio.run(read_catalog(None)).reason == "회사 상품 목록을 주는 커넥터가 없음"
    failed = asyncio.run(read_catalog(FakeCatalog(fail=True)))
    assert failed.products == [] and failed.reason == "회사 상품 목록을 읽지 못함 [UNAVAILABLE]"
    sold_out = [CatalogProduct(product_id="x", name="x", active=False)]
    assert asyncio.run(read_catalog(FakeCatalog(sold_out))).reason == "회사 상품 목록이 비어 있음"
    ok = asyncio.run(read_catalog(FakeCatalog()))
    assert ok.reason is None and len(ok.products) == 4 and ok.categories == CATEGORIES


class NoEmbed:
    async def embed(self, texts: list[str], context_id: str | None = None) -> list[list[float]]:
        raise LLMNotConfiguredError("DATA_EMBED_MODEL 또는 EMBED_MODEL이 없다")


def test_similarities_need_an_embedding_setting() -> None:
    got = asyncio.run(similarities(NoEmbed(), {"t1": "t1"}, PRODUCTS, CATEGORIES))  # type: ignore[arg-type]
    assert got.reason == "임베딩 설정이 없음 (DATA_EMBED_MODEL 또는 EMBED_MODEL)"
    off = asyncio.run(
        similarities(MockLLMClient(), {"t1": "t1"}, PRODUCTS, CATEGORIES, enabled=False)
    )
    assert off.reason == "회사 설정에서 유사도를 끔"
    mock = asyncio.run(
        similarities(MockLLMClient(), {"파이썬": "파이썬 입문"}, PRODUCTS, CATEGORIES)
    )
    assert mock.reason is None and len(mock.by_topic["파이썬"]) == 4
    assert all(-1.0 <= s <= 1.0 for s in mock.by_topic["파이썬"])


def test_shop_connector_reads_products_and_category_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/shop/products":
            return httpx.Response(200, json={"data": [
                {"product_id": "prd_0001", "seller_id": "s", "name": "파이썬 입문서",
                 "category_code": "book", "price": 1, "stock": 1, "active": True}]})  # fmt: skip
        return httpx.Response(200, json={"data": [
            {"category_code": "book", "name": "도서", "product_count": 4},
            {"category_code": "etc", "name": None, "product_count": 1}]})  # fmt: skip

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    shop = ShopApiConnector(ShopApi("http://shop", token="t", client=client))
    [product] = asyncio.run(shop.products())
    assert (product.product_id, product.name, product.category_code) == (
        "prd_0001",
        "파이썬 입문서",
        "book",
    )
    assert asyncio.run(shop.categories()) == {"book": "도서"}  # 이름 없는 코드는 뺀다


# ---------------------------------------------------------------- 아이디어 문장


def test_an_idea_naming_an_unlinked_company_product_is_replaced() -> None:
    llm = MockLLMClient(
        responses={_Idea: _Idea(idea="독서대를 놓고 공부하는 영상을 찍어 보세요.", format="숏폼")}
    )  # type: ignore[arg-type]
    names = {"키워드": "파이썬", "연결 상품": "파이썬 입문서"}
    idea = asyncio.run(explain_idea({"배수": 2.0}, names, llm, catalog=[p.name for p in PRODUCTS]))
    assert idea.fallback and idea.reason == PRODUCT_INVALID
    ok_llm = MockLLMClient(
        responses={_Idea: _Idea(idea="파이썬 입문서로 첫 코드를 짜 보는 영상.", format="숏폼")}
    )  # type: ignore[arg-type]
    ok = asyncio.run(explain_idea({"배수": 2.0}, names, ok_llm, catalog=[p.name for p in PRODUCTS]))
    assert not ok.fallback


# ---------------------------------------------------------------- skill


def _points(spike: float) -> tuple[tuple[date, float], ...]:
    out = []
    for i in range(REQUEST_DAYS):
        v = 50 * (1 + 0.05 * math.sin(i * 2.1)) * (spike if i >= REQUEST_DAYS - 3 else 1.0)
        out.append((START + timedelta(days=i), round(v, 4)))
    return tuple(out)


class Naver:
    def __init__(self, spikes: dict[str, float]) -> None:
        self.spikes, self.calls_today = spikes, 0

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.calls_today += 1
        name = query.keyword_groups[0].group_name
        series = TrendSeries(name, (name,), _points(self.spikes.get(name, 1.0)))
        return TrendResponse(query.start_date, query.end_date, query.time_unit, (series,))


def _env(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "trends.toml"
    config.write_text(
        '[[groups]]\nname = "파이썬"\nkeywords = ["파이썬"]\n\n'
        '[[groups]]\nname = "t1"\nkeywords = ["t1"]\n\n'
        "[recommend]\nmax_items = 5\nmax_products = 2\nmin_similarity = 0.99\n"
        'company_keywords = ["t1"]\n',
        encoding="utf-8",
    )
    return {"DATA_YOUTUBE_DB": str(tmp_path / "none.db"), "DATA_TREND_CONFIG": str(config)}


def test_recommendations_carry_linked_products_and_relevance_basis(tmp_path: Path) -> None:
    llm = MockLLMClient(
        responses={_Idea: _Idea(idea="파이썬 입문서로 따라 하는 영상.", format="숏폼")}
    )  # type: ignore[arg-type]
    out = asyncio.run(
        recommend(
            ContentRecommendRequest(end_date=END),
            Naver({"파이썬": 3.0, "t1": 3.0}),
            llm,
            env=_env(tmp_path),
            now=NOW,
            catalog=FakeCatalog(),
        )  # fmt: skip
    ).output
    by = {i.keyword: i for i in out.items}
    py, t1 = by["파이썬"], by["t1"]
    assert [(p.product_id, p.name, p.match) for p in py.products] == [
        ("prd_1", "파이썬 입문서", "name")
    ]
    # 파이썬은 회사 기본 키워드 그룹이라 수동 키워드에도 걸린다 → 둘 다
    assert py.company_relevance == "related" and py.relevance_from == "both"
    # t1: 연결 상품은 없지만 수동 회사 키워드(덮어쓰기용)로 related
    assert t1.products == [] and t1.relevance_from == "manual_keywords"
    assert (out.catalog_status, out.catalog_products, out.catalog_reason) == ("known", 4, None)
    # MockLLMClient는 임베딩을 주므로 유사도는 쟀다 (기준 0.99라 유사도만으로는 잇지 않음)
    assert out.similarity_status == "known"
    # 연결 상품은 데이터 블록으로만 들어간다
    prompt = next(c for c in llm.calls if "파이썬 입문서" in c)
    assert "파이썬 입문서" in prompt.partition("<untrusted")[2]
    assert "파이썬 입문서" not in prompt.partition("<untrusted")[0]


def test_without_a_catalog_relevance_falls_back_to_manual_keywords(tmp_path: Path) -> None:
    llm = MockLLMClient(responses={_Idea: _Idea(idea="영상 아이디어.", format="숏폼")})  # type: ignore[arg-type]
    out = asyncio.run(
        recommend(
            ContentRecommendRequest(end_date=END),
            Naver({"파이썬": 3.0}),
            llm,
            env=_env(tmp_path),
            now=NOW,
            catalog=FakeCatalog(fail=True),
        )  # fmt: skip
    ).output
    [item] = out.items
    # 상품을 못 읽으면 수동 회사 키워드(기본 그룹 이름)로만 본다
    assert item.products == [] and item.company_relevance == "related"
    assert item.relevance_from == "manual_keywords"
    assert out.catalog_status == "unknown"
    assert out.catalog_reason == "회사 상품 목록을 읽지 못함 [UNAVAILABLE]"


def test_company_config_has_product_link_settings() -> None:
    config = load_config({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)})
    assert 1 <= config.max_products <= 5 and 0 < config.min_similarity < 1
    assert config.company_keywords  # 수동 회사 키워드는 덮어쓰기용으로 남는다


def test_handler_passes_the_company_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: Naver({"파이썬": 3.0}))
    monkeypatch.setattr(handlers, "get_catalog", lambda: FakeCatalog())
    monkeypatch.setattr(
        handlers,
        "get_llm",
        lambda: MockLLMClient(responses={_Idea: _Idea(idea="아이디어.", format="숏폼")}),
    )  # type: ignore[arg-type]
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    meta = RequestMeta(
        context_id="ctx_prd", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    request = AgentRequest(
        meta=meta, skill="content_recommend", input={"end_date": END.isoformat()}
    )
    result = asyncio.run(handlers.content_recommend(request))
    assert result.status == TaskStatus.COMPLETED
    items = result.output["items"]
    assert items and items[0]["products"][0]["product_id"] == "prd_1"
