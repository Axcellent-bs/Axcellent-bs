"""campaign_effect 평가 케이스(`eval/campaign/`)의 약속을 지키는지 본다.

- 정답(방향·배수·쿠폰 조건)은 주입 조건에서 나온다
- 쿠폰 조건과 생성기가 만든 기록이 맞는다(발급 장수, 구간 뒤 사용 장수)
- 다시 만들면 커밋된 파일과 같다 (데이터를 다시 만들어 느려서 `eval` 표시, data-eval job이 돈다)
holdout은 파일 내용을 사람이 열지 않는다. 여기서는 기계로 개수·조건 일치·다시 만든 결과만 본다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

CAMPAIGN = Path(__file__).resolve().parents[1] / "eval" / "campaign"


def _make() -> ModuleType:
    spec = importlib.util.spec_from_file_location("campaign_make_cases", CAMPAIGN / "make_cases.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((CAMPAIGN / folder).glob("*.json"))
    }


def _all() -> list[dict]:
    return [*_files("cases").values(), *_files("holdout").values()]


def _factor(case: dict) -> float:
    injection = case["spec"]["injection"]
    return (
        float(injection.get("factor", 1.0)) if injection["type"] in ("sale", "order_drop") else 1.0
    )


def test_order_answers_come_from_the_injection() -> None:
    for case in _all():
        orders = case["expected"]["orders"]
        if case["spec"]["injection"]["type"] == "empty":
            assert orders == {"status": "unknown", "unknown_reason": "no_history"}
            continue
        factor = _factor(case)
        want = "up" if factor > 1 else "down" if factor < 1 else "none"
        assert orders == {"status": "known", "direction": want, "multiplier": factor}
        assert case["expected"]["revenue"] == {"status": "known", "direction": want}


def test_coupon_answers_come_from_the_conditions_and_match_the_records() -> None:
    make = _make()
    for case in _all():
        coupon, counted = case["expected"]["coupon"], case["counted"]
        campaign = case["spec"].get("campaign")
        if case["input"]["campaign_id"] is None:
            assert coupon == {"status": "unknown", "unknown_reason": "no_campaign_id"}
        elif campaign is None:
            assert coupon == {"status": "unknown", "unknown_reason": "no_coupons"}
            assert counted["coupon_issued_units"] == 0  # 0%가 아니라 확인할 수 없음이 정답
        else:
            assert coupon["issued_units"] == campaign["buyers"] * campaign["units_each"]
            assert coupon["used_after_units"] == campaign["used_after"]
            assert coupon["note"] == make.NOTE_CUMULATIVE
            assert counted["coupon_issued_units"] == coupon["issued_units"]
            assert counted["coupon_used_after"] == coupon["used_after_units"]
            used = counted["coupon_used_in_window"] + counted["coupon_used_after"]
            assert counted["coupon_used_units"] == used
            assert counted["coupon_use_rate"] == round(used / coupon["issued_units"], 4)


def test_cases_cover_the_plan_and_holdout_is_large_enough() -> None:
    kinds = [c["kind"] for c in _files("cases").values()]
    assert {"up", "none", "down", "unknown"} <= set(kinds)
    reasons = {
        e["unknown_reason"]
        for c in _files("cases").values()
        for e in c["expected"].values()
        if e["status"] == "unknown"
    }
    assert {"no_history", "no_campaign_id", "no_coupons"} <= reasons
    held = _files("holdout")
    assert len(held) >= 10
    assert {c["holdout"]["seed"] for c in held.values()} == {_make().HOLDOUT_SEED}


@pytest.mark.eval
def test_cases_are_rebuilt_from_the_generator() -> None:
    make = _make()
    assert {c["id"]: c for c in make.build_cases()} == _files("cases")
    assert {c["id"]: c for c in make.build_holdout()} == _files("holdout")
