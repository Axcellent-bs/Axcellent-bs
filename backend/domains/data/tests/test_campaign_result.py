"""campaign_result: 성과 결과 1건 (campaign_id 기준). 실제 YouTube·쇼핑몰은 안 부른다 (가짜).

- 영상 성과는 저장분 그대로, 매출은 campaign_effect 계산(여기서는 가짜 measure) + 판정
- 부서별 묶음(view): 기획 ①~⑤, 마케팅 ① + 개선 방향만, 재무 ①③⑤
- 프로모션 링크·쿠폰이 없는 영상은 매출 측정 불가, 캠페인 줄 "영상 N개 중 매출 측정 가능 M개"
- 영상이 없거나 쇼핑몰 연결이 없으면 지어내지 않고 이유
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import (
    CampaignEffectInput,
    CampaignEffectOutput,
    CampaignResultRequest,
    VideoPerformanceRequest,
)
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import campaign_result as cr
from data_agent.skills import video_performance as vp

PUB = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
EVAL = Path(__file__).resolve().parents[1] / "eval" / "campaign_result" / "make_cases.py"


def _cases():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("campaign_result_cases_unit", EVAL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeYouTube:
    def __init__(self) -> None:
        self.now = PUB

    async def videos(self, ids):  # noqa: ANN001, ANN201
        hours = (self.now - PUB).total_seconds() / 3600
        views = int(2000 * hours)
        return [VideoStat(i, "ch", "24", PUB, views, views // 20, views // 100, (), title=i)
                for i in ids]  # fmt: skip


def _env(tmp: Path) -> dict[str, str]:
    return {vp.DB_ENV: str(tmp / "perf.db"), "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db"),
            "DATA_YOUTUBE_DB": str(tmp / "youtube.db"),
            "DATA_SHORTS_DB": str(tmp / "shorts.db")}  # fmt: skip


def _setup(tmp: Path) -> dict[str, str]:
    """인사이트(목표 5000, 연결 상품 2개) + 캠페인 cmp_1 영상 2개(하나만 promo) + 시험 영상."""
    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    insight = {"insight_id": "ins_1", "rank": 1, "topic": "파이썬 입문", "line": "x",
               "shorts_format": "강의", "target_views_reference": 5000.0,
               "products": [{"product_id": "p1", "name": "파이썬 책", "reason": "x"},
                            {"product_id": "p2", "name": "코딩 책", "reason": "x"}]}  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-01','r','x',?,NULL)",
                 (json.dumps({"insights": [insight]}),))  # fmt: skip
    conn.commit()
    conn.close()
    env = _env(tmp)
    yt = FakeYouTube()
    for url, campaign, promo, test in (
        ("https://youtu.be/aaaaaaaaaaa", "cmp_1", "promo-1", False),
        ("https://youtu.be/bbbbbbbbbbb", "cmp_1", None, False),
        ("https://youtu.be/ttttttttttt", "test-video-1", None, True),
    ):
        req = VideoPerformanceRequest(video_url=url, campaign_id=campaign, insight_id="ins_1",
                                      promo_id=promo, test=test)  # fmt: skip
        asyncio.run(vp.video_performance(req, yt, env=env, now=PUB))
    yt.now = PUB + timedelta(hours=168, minutes=10)
    store = vp.store_of(env)
    asyncio.run(vp.measure_due(store, yt, yt.now))
    store.close()
    return env


def _measure(case: str, seen: list[CampaignEffectInput]):  # noqa: ANN202
    effect = CampaignEffectOutput.model_validate(_cases().effect_of(case))

    async def fake(data: CampaignEffectInput, connector, *, env):  # noqa: ANN001, ANN202, ARG001
        seen.append(data)
        return effect

    return fake


def _run(env: dict, view: str = "planning", case: str = "views_up_sales_down",
         connector: object = "shop", campaign: str = "cmp_1"):  # noqa: ANN202 # fmt: skip
    seen: list[CampaignEffectInput] = []
    out = asyncio.run(cr.campaign_result(
        CampaignResultRequest(campaign_id=campaign, view=view), connector, env=env,
        now=PUB + timedelta(days=9), measure=_measure(case, seen))).output  # fmt: skip
    return out, seen


def test_planning_gets_everything_with_a_loss_alert(tmp_path: Path) -> None:
    out, seen = _run(_setup(tmp_path))
    assert out.status == "known"
    assert out.sections == ["link", "videos", "sales", "advice", "alerts"]  # 기획: ①~⑤
    assert (out.campaign_name, out.insight_id, out.schema_version) == (
        "파이썬 입문", "ins_1", "campaign_result.v2")  # fmt: skip
    assert out.window_from == PUB and out.window_to == PUB + timedelta(days=7)  # 첫 게시 ~ 7일
    (data,) = seen
    assert data.products == ["p1", "p2"] and data.campaign_id == "cmp_1"  # 인사이트 연결 상품
    assert {v.video_id for v in out.videos} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}  # 시험 캠페인 빼고
    sales = out.sales
    assert sales is not None and sales.verdict is not None
    assert (sales.verdict.verdict, sales.verdict.label) == ("less", "덜 팔림")
    assert sales.verdict.incremental_gross_krw == -700_000  # 마이너스 그대로
    assert sales.measurable_line == "영상 2개 중 매출 측정 가능 1개"
    assert [c.key for c in sales.cells] == ["attribution", "products", "coupons", "store"]
    assert sales.headline == "-700,000원 · 주문 -15건 (시작 전 4주 같은 요일·시간대 대비)"
    (loss,) = [a for a in out.alerts if a.kind == "loss"]
    assert loss.message == "파이썬 입문 캠페인 매출이 평소보다 줄었습니다"
    assert "사람이 정합니다" in (loss.note or "")
    b = next(v for v in out.videos if v.video_id == "bbbbbbbbbbb")
    assert b.sales_reason == "측정 불가: 프로모션 링크/쿠폰 없음"
    assert out.advice and all(a.factors for a in out.advice)
    assert any("YouTube Analytics" in lim for lim in out.limits)
    assert any("더하지 않습니다" in lim for lim in out.limits)


def test_marketing_and_finance_get_their_own_bundle(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    mk, _ = _run(env, view="marketing")
    assert mk.sections == ["link", "advice"] and mk.videos == [] and mk.sales is None
    assert mk.alerts == [] and mk.advice and all(not a.factors and a.actions for a in mk.advice)
    fi, _ = _run(env, view="finance")
    assert fi.sections == ["link", "sales", "alerts"] and fi.videos == [] and fi.advice == []
    assert fi.sales is not None and fi.alerts and fi.window_from == PUB


def test_finance_bundle_has_the_record_values_data_knows(tmp_path: Path) -> None:
    """v2 재무 묶음 finance_record: 증가분이 음수면 gmv·orders 0, 잰 값은 그대로, 집행액 칸 없음."""
    from ax_contracts.schemas.finance import CampaignResultInput

    env = _setup(tmp_path)
    fi, _ = _run(env, view="finance")
    rec = fi.finance_record
    assert rec is not None and fi.schema_version == "campaign_result.v2"
    assert (rec.campaign_id, rec.period_from, rec.period_to) == ("cmp_1", "2026-11-01",
                                                                 "2026-11-08")  # fmt: skip
    assert (rec.gmv_krw, rec.orders) == (0, 0)  # 줄었으면 0 (재무 계약 ge=0)
    assert (rec.measured_gmv_krw, rec.measured_orders) == (-700_000, -15)  # 잰 값 그대로
    assert rec.source == "data.campaign_result"
    body = rec.model_dump(mode="json")
    assert "spend_krw" not in body and "campaign_name" not in body  # 마케팅이 채움
    CampaignResultInput.model_validate({**{k: v for k, v in body.items() if v is not None},
                                        "campaign_name": "파이썬 입문", "channel": "youtube",
                                        "spend_krw": 1})  # fmt: skip
    pl, _ = _run(env, view="planning")
    assert pl.finance_record is None  # 재무 묶음만
    for out in (fi, pl):  # 기능 플래그가 없으면(기본) 자동 보내기 꺼짐
        assert out.send_status is not None and out.send_status.state == "off"
        assert (out.send_status.chip, out.send_status.line) == ("자동 보내기 꺼짐",
                                                                "safety 확인 후 켭니다")


def test_low_sample_is_not_a_loss(tmp_path: Path) -> None:
    out, _ = _run(_setup(tmp_path), case="low_sample_negative")
    assert out.sales is not None and out.sales.verdict is not None
    assert out.sales.verdict.label == "효과 확인 안 됨(표본 적음)" and out.sales.verdict.low_sample
    assert out.sales.verdict.incremental_gross_krw == -60_000
    assert not [a for a in out.alerts if a.kind == "loss"]


def test_no_videos_or_no_shop_say_why(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    none, seen = _run(env, campaign="cmp_none")
    assert none.status == "unknown" and none.reason == "캠페인 cmp_none에 등록된 영상이 없음"
    assert seen == []
    shopless, seen = _run(env, connector=None)
    assert seen == [] and shopless.sales is not None and shopless.sales.verdict is not None
    assert shopless.sales.verdict.label == "측정 불가: 쇼핑몰 연결이 없음"
    assert shopless.sales.cells == [] and shopless.videos  # 영상 성과는 그대로


def test_test_campaign_is_marked(tmp_path: Path) -> None:
    out, _ = _run(_setup(tmp_path), campaign="test-video-1")
    assert out.test and out.sales is not None and out.sales.videos_total == 0
    assert out.advice == []  # 시험 영상은 성과 요인·개선 방향 없음
