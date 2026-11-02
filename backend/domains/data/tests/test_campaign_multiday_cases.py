"""campaign_effect 여러 날 캠페인 평가 케이스(`eval/campaign/multiday/`)의 약속을 지키는지 본다.

- 정답(방향·배수)은 주입 조건에서, 기대(`counted.orders_baseline`)는 같은 요일·같은 시간대 정의로
- 쿠폰 조건과 생성기 기록이 맞는다(발급 장수, 캠페인 뒤 사용 장수)
- 계획한 케이스(2·3·7일, 주말 포함, 작은 효과, 짧은 기록)가 다 있다
- 다시 만들면 커밋된 파일과 같다 (데이터를 다시 만들어 느려서 `eval` 표시, data-eval job이 돈다)
holdout은 파일 내용을 사람이 열지 않는다. 여기서는 기계로 개수·조건 일치·다시 만든 결과만 본다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

CAMPAIGN = Path(__file__).resolve().parents[1] / "eval" / "campaign"
MULTIDAY = CAMPAIGN / "multiday"


def _make() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "campaign_make_multiday", CAMPAIGN / "make_multiday.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((MULTIDAY / folder).glob("*.json"))
    }


def _all() -> list[dict]:
    return [*_files("cases").values(), *_files("holdout").values()]


def _days(case: dict) -> int:
    start = datetime.fromisoformat(case["input"]["window_from"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(case["input"]["window_to"].replace("Z", "+00:00"))
    return round((end - start) / timedelta(days=1))


def test_answers_come_from_the_injection() -> None:
    for case in _all():
        injection, traffic = case["spec"]["injection"], case["spec"]["traffic"]
        factor = (
            float(injection.get("factor", 1.0))
            if injection["type"] in ("sale", "order_drop")
            else 1.0
        )
        if traffic["history_days"] < 14:
            assert case["expected"]["orders"] == {
                "status": "unknown",
                "unknown_reason": "short_history",
            }
            assert case["counted"]["orders_baseline"] is None  # 반쯤 빈 평균을 넣지 않는다
            continue
        want = "up" if factor > 1 else "down" if factor < 1 else "none"
        assert case["expected"]["orders"] == {
            "status": "known",
            "direction": want,
            "multiplier": factor,
        }
        assert case["expected"]["revenue"] == {"status": "known", "direction": want}
        assert case["counted"]["orders_baseline"] > 0


def test_coupon_conditions_match_the_records() -> None:
    for case in _all():
        coupon, counted, campaign = (
            case["expected"]["coupon"],
            case["counted"],
            case["spec"].get("campaign"),
        )
        if coupon["status"] != "known":
            continue
        assert (
            coupon["issued_units"]
            == campaign["buyers"] * campaign["units_each"]
            == counted["coupon_issued_units"]
        )
        assert coupon["used_after_units"] == campaign["used_after"] == counted["coupon_used_after"]


def test_every_case_has_fourteen_days_of_same_weekday_history_or_says_why_not() -> None:
    for case in _all():
        traffic = case["spec"]["traffic"]
        assert (
            traffic["weekday_levels"][5] > traffic["weekday_levels"][0]
        )  # 주말이 높다 (요일 효과)
        assert 2 <= _days(case) <= 7  # 같은 요일 7일 전·14일 전이 캠페인 전에 있으려면 7일 이하
        assert traffic["history_days"] >= 14 or case["expected"]["orders"]["status"] == "unknown"


def test_cases_cover_the_plan_and_holdout_is_separate() -> None:
    cases = list(_files("cases").values())
    assert {2, 3, 7} <= {_days(c) for c in cases}
    multipliers = {c["expected"]["orders"].get("multiplier") for c in cases}
    assert {2.0, 1.0} <= multipliers and any(m and m < 1 for m in multipliers)
    assert any(m and 1.1 <= m <= 1.2 for m in multipliers)  # 작은 효과
    weekend = [
        c for c in cases if "토" in c["memo"] and c["expected"]["orders"].get("direction") == "none"
    ]
    assert weekend  # 주말이 낀 변화 없음 케이스 (요일 효과를 기준선이 처리하는지)
    assert any(c["expected"]["orders"].get("unknown_reason") == "short_history" for c in cases)
    held = _files("holdout")
    assert len(held) >= 10
    make = _make()
    assert {c["holdout"]["seed"] for c in held.values()} == {make.HOLDOUT_SEED}
    assert make.HOLDOUT_SEED != 2026110203  # 기존 campaign holdout과 다른 seed


@pytest.mark.eval
def test_cases_are_rebuilt_from_the_generator() -> None:
    make = _make()
    assert {c["id"]: c for c in make.build_cases()} == _files("cases")
    assert {c["id"]: c for c in make.build_holdout()} == _files("holdout")
