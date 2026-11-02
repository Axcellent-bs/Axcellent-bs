"""campaign_effect 채점기(`eval/campaign/score.py`) 테스트. skill 없이 가짜 판정 함수로 돈다.

holdout은 사람이 열지 않는다. 여기서는 정답을 그대로 돌려주는 가짜 판정이 기계로만 읽는다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

CAMPAIGN = Path(__file__).resolve().parents[1] / "eval" / "campaign"
NOTE = "조회 시점 기준 누적 사용률이며 캠페인 뒤 사용분도 섞인다"


def _score() -> ModuleType:
    spec = importlib.util.spec_from_file_location("campaign_score", CAMPAIGN / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score = _score()


def _key(request: dict[str, Any]) -> tuple:
    return (request["campaign_id"], request["window_from"], request["window_to"])


def _truth_answer(case: dict[str, Any], *, note: str | None = NOTE) -> dict[str, Any]:
    """케이스 정답·센 값을 그대로 옮긴 답 (채점 계산 확인용)."""
    exp, counted = case["expected"], case["counted"]

    def part(item: str, excess_key: str | None) -> dict[str, Any]:
        want = exp[item]
        if want["status"] == "unknown":
            return {"status": "unknown", "unknown_reason": want["unknown_reason"]}
        out: dict[str, Any] = {"status": "known", "direction": want["direction"]}
        if excess_key:
            out["excess"] = counted[f"{excess_key}_actual"] - counted[f"{excess_key}_baseline"]
        return out

    coupon = exp["coupon"]
    return {
        "orders": part("orders", "orders"),
        "revenue": part("revenue", "revenue"),
        "coupon": (
            {"status": "unknown", "unknown_reason": coupon["unknown_reason"]}
            if coupon["status"] == "unknown"
            else {"status": "known", "use_rate": counted["coupon_use_rate"], "note": note}
        ),
    }


def oracle(**kwargs):  # noqa: ANN003, ANN201
    answers = {
        _key(c["input"]): _truth_answer(c, **kwargs)
        for folder in (score.CASES, score.HOLDOUT)
        for c in score.load(folder)
    }

    def judge(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        return answers[_key(request)]

    return judge


def always_up(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
    part = {"status": "known", "direction": "up", "excess": 10.0}
    return {
        "orders": part,
        "revenue": part,
        "coupon": {"status": "known", "use_rate": 0.5, "note": NOTE},
    }


def _run(judge) -> dict[str, Any]:  # noqa: ANN001
    return asyncio.run(score.run(judge))


def test_truth_answers_score_full_marks() -> None:
    report = _run(oracle())
    m = report["metrics"]
    assert m["campaign.order_direction_rate"] == 1.0
    assert m["campaign.order_excess_error"] == 0.0
    assert m["campaign.unknown_handled_rate"] == 1.0
    assert m["campaign.coupon_usage_match"] == 1.0
    assert m["holdout.campaign.order_direction_rate"] == 1.0
    assert m["holdout.campaign.coupon_usage_match"] == 1.0
    assert report["wrong"] == [] and report["holdout"]["wrong_count"] == 0


def test_always_up_scores_low_on_direction() -> None:
    m = _run(always_up)["metrics"]
    cases = score.load(score.CASES)
    ups = sum(c["expected"]["orders"].get("direction") == "up" for c in cases)
    known = sum(c["expected"]["orders"]["status"] == "known" for c in cases)
    assert m["campaign.order_direction_rate"] == round(ups / known, 4) < 1.0
    assert m["campaign.unknown_handled_rate"] == 0.0  # 모를 때도 안다고 답했다
    assert m["campaign.coupon_usage_match"] < 1.0
    assert m["holdout.campaign.order_direction_rate"] < 1.0


def test_coupon_needs_the_cumulative_note() -> None:
    m = _run(oracle(note="쿠폰 사용률입니다"))["metrics"]
    assert m["campaign.coupon_usage_match"] == 0.0
    assert m["campaign.order_direction_rate"] == 1.0  # 다른 지표는 그대로
    assert score.note_ok("조회 시점 기준 누적 사용률이며 캠페인 뒤 사용분도 섞입니다")
    assert not score.note_ok("누적 사용률")  # 캠페인 뒤 섞임 설명이 빠짐


def test_revenue_stays_out_of_the_main_metrics() -> None:
    """매출을 확인할 수 없다고 답해도(지금 정상) 본 지표는 그대로다."""

    def unknown_revenue(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        answer = oracle()(request, transport)
        return answer | {"revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"}}

    report = _run(unknown_revenue)
    m = report["metrics"]
    assert m["campaign.order_direction_rate"] == 1.0
    assert m["campaign.unknown_handled_rate"] == 1.0
    assert m["campaign.revenue_unknown_rate"] == 1.0 and m["campaign.revenue_direction_rate"] == 0.0
    assert report["wrong"] == []  # 매출은 틀린 목록에도 넣지 않는다


def test_errors_and_bad_shapes_count_as_wrong() -> None:
    def broken(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        raise RuntimeError("판정 실패")

    def bad_shape(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        return {"orders": "늘었음"}

    for judge in (broken, bad_shape):
        report = _run(judge)
        assert report["metrics"]["campaign.order_direction_rate"] == 0.0
        assert report["metrics"]["campaign.order_excess_error"] is None  # 낸 증분이 없다
        assert all(w["got"] == "missing" for w in report["wrong"])


def test_excess_error_is_the_mean_absolute_gap() -> None:
    truth = oracle()

    def off_by_ten(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        answer = truth(request, transport)
        if answer["orders"]["status"] == "known":
            answer = answer | {
                "orders": answer["orders"] | {"excess": answer["orders"]["excess"] + 10}
            }
        return answer

    assert _run(off_by_ten)["metrics"]["campaign.order_excess_error"] == 10.0


def test_judge_reads_the_case_data_through_shop_api_routes() -> None:
    """판정 함수는 케이스 데이터로 만든 shop-api 응답을 받는다 (실제 skill처럼 읽게)."""
    seen: list[int] = []

    async def reader(request: dict[str, Any], transport: httpx.MockTransport) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport, base_url="http://shop.test") as client:
            params = {"campaign_id": request["campaign_id"]} if request["campaign_id"] else {}
            coupons = (await client.get("/api/shop/coupons", params=params)).json()["data"]
            series = await client.get(
                "/api/shop/metric-series",
                params={
                    "metric": "order_count",
                    "from": request["window_from"],
                    "to": request["window_to"],
                },
            )
        seen.append(series.status_code)
        rate = None
        if request["campaign_id"] and coupons:
            rate = sum(c["used_units"] for c in coupons) / sum(c["count"] for c in coupons)
        part = {"status": "known", "direction": "up"}
        coupon = (
            {"status": "known", "use_rate": round(rate, 4), "note": NOTE}
            if rate is not None
            else {"status": "unknown", "unknown_reason": "쿠폰 없음"}
        )
        return {"orders": part, "revenue": part, "coupon": coupon}

    m = _run(reader)["metrics"]
    assert seen and set(seen) == {200}
    assert m["campaign.coupon_usage_match"] == pytest.approx(1.0, abs=0.0)  # 실제 쿠폰 응답 = 센 값


def test_holdout_shows_only_the_wrong_count() -> None:
    report = _run(always_up)
    text = score.render(report)
    assert "holdout_" not in text
    assert f"틀린 항목 {report['holdout']['wrong_count']}" in text
    assert set(report["holdout"]) == {"seed", "cases", "wrong_count"}
