"""기회 카드 실제 소비 근거: 쇼핑인사이트 클릭 추이 + 연령대별 평소 대비 배수, 하루 몫, 다시 확인.

- 클릭 추이: 분야 안 키워드 클릭의 평소 대비 배수 (검색어 트렌드와 같은 판정 함수)
- 연령대별: 연령대마다 그 연령대의 평소 대비 배수. 연령대끼리 양은 비교하지 않는다
- 쇼핑 호출은 네이버 전체 하루 상한(검색어 트렌드와 같은 카운터)에 호출자별로 센다.
  기회 카드 몫(`[opportunity] shopping_daily_calls`)을 다 쓰면 부르지 않고 이유
- 숫자는 카드 facts에 들어간다
- data_sources recheck: 저장된 실패가 있어도 지금 1번 다시 확인
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_contracts.schemas.data import PlanCategory, SourcePlanItem
from data_agent.connectors import naver_datalab, naver_shopping
from data_agent.skills.data_sources import ProbeStore, list_sources
from data_agent.skills.domain_opportunity import age_label, default_consumption

END = date(2026, 11, 5)
NAVER_ENV = {"NAVER_HUB_CLIENT_ID": "test-id", "NAVER_HUB_CLIENT_SECRET": "test-secret"}
ITEM = SourcePlanItem(source_id="naver_shopping", status="available", approved=True,
                      reason="쇼핑", keywords=["고양이"],
                      categories=[PlanCategory(category_id="50005542", name="도서")])  # fmt: skip


def _points(rise: float) -> list[dict]:
    """평소 20, 최근 7일 20 × rise (평소 대비 배수 = rise)."""
    out = []
    for i in range(63, -1, -1):
        d = END - timedelta(days=i)
        out.append({"period": d.isoformat(), "ratio": 20.0 * (rise if i < 7 else 1.0)})
    return out


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if request.url.path.endswith("/category/keyword/age"):
        data = [{**p, "group": "20"} for p in _points(2.0)]
        data += [{**p, "group": "60"} for p in _points(1.0)]
        return httpx.Response(200, json={"results": [{"title": body["keyword"], "data": data}]})
    kw = body["keyword"][0]["name"]
    return httpx.Response(200, json={"results": [
        {"title": kw, "keyword": [kw], "data": _points(1.5)}]})  # fmt: skip


@pytest.fixture
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _handler(request)

    naver_shopping.use_client(httpx.AsyncClient(transport=httpx.MockTransport(record)))
    naver_shopping.clear_cache()
    naver_datalab.clear_cache()
    yield seen
    naver_shopping.use_client(None)
    naver_shopping.clear_cache()
    naver_datalab.clear_cache()


def _env(tmp_path: Path, share: int | None = None) -> dict[str, str]:
    env = dict(NAVER_ENV)
    if share is not None:
        cfg = tmp_path / "trends.toml"
        cfg.write_text(f"[opportunity]\nshopping_daily_calls = {share}\n", encoding="utf-8")
        env["DATA_TREND_CONFIG"] = str(cfg)
    return env


def test_clicks_and_age_bands_are_own_baseline_ratios(
    tmp_path: Path, shop: list[httpx.Request]
) -> None:
    got = asyncio.run(default_consumption(_env(tmp_path), END)(ITEM, "고양이"))
    assert isinstance(got, list)
    by = {e.label: e for e in got}
    assert by["쇼핑 클릭 평소 대비 배수"].value == pytest.approx(1.5)
    assert by["쇼핑 클릭 평소 대비 배수 · 20대"].value == pytest.approx(2.0)
    assert by["쇼핑 클릭 평소 대비 배수 · 60대 이상"].value == pytest.approx(1.0)
    assert all(e.status == "known" and e.unit == "배" and e.source_id == "naver_shopping"
               for e in got)  # fmt: skip
    # 분야는 계획이 고른 도서, 키워드 그대로
    bodies = [json.loads(r.content) for r in shop]
    assert {b["category"] for b in bodies} == {"50005542"}
    # 네이버 전체 하루 상한에 기회 카드 몫으로 센다
    assert naver_datalab.used_today("shopping:domain_opportunity") == 2
    assert naver_datalab._cache.calls == 2


def test_share_used_up_gives_a_reason_without_calling(
    tmp_path: Path, shop: list[httpx.Request]
) -> None:
    fetch = default_consumption(_env(tmp_path, share=2), END)
    asyncio.run(fetch(ITEM, "고양이"))
    calls = len(shop)
    (again,) = asyncio.run(fetch(ITEM, "강아지"))
    assert len(shop) == calls and again.status == "unknown"
    assert "몫(2번)을 다 씀" in (again.reason or "")


def test_age_labels() -> None:
    assert age_label("10") == "10대" and age_label("60") == "60대 이상"


def test_recheck_probes_again_despite_a_stored_failure(tmp_path: Path) -> None:
    store = ProbeStore(tmp_path / "probe.db")
    now = datetime(2026, 11, 5, 1, tzinfo=UTC)
    store.put("naver_shopping", False, "UNAUTHORIZED: 이용 신청 전", now - timedelta(hours=2))
    asked: list[str] = []

    async def prober(source_id: str) -> tuple[bool, str]:
        asked.append(source_id)
        return True, "쇼핑인사이트 호출 성공"

    plain = asyncio.run(list_sources(NAVER_ENV, prober=prober, now=now, store=store))
    assert asked == []  # 실패 뒤 하루 안에는 다시 부르지 않는다
    shop = next(s for s in plain.output.sources if s.source_id == "naver_shopping")
    assert shop.status == "key_required"
    again = asyncio.run(list_sources(NAVER_ENV, prober=prober, now=now, store=store,
                                     recheck=True))  # fmt: skip
    assert asked == ["naver_shopping"]
    shop = next(s for s in again.output.sources if s.source_id == "naver_shopping")
    assert shop.status == "available"
    store.close()
