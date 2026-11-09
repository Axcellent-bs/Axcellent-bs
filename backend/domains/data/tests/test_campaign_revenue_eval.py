"""campaign_effect 매출 평가 정의(`eval/campaign_revenue/`)의 약속을 지키는지 본다.

- 정답(방향·공백)은 주입 조건에서 나온다 (factor × 수량 배수, 구간 결제 공백 = unknown)
- 채점기: 맞는 답은 1.0·오차 0, 틀린 답·공백을 known으로 답하면 틀림, 판정 실패는 missing
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

REVENUE = Path(__file__).resolve().parents[1] / "eval" / "campaign_revenue"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"campaign_revenue_{name}", REVENUE / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((REVENUE / folder).glob("*.json"))
    }


def _all() -> list[dict]:
    return [*_files("cases").values(), *_files("holdout").values()]


def test_answers_come_from_the_injection() -> None:
    cases = _all()
    assert len(_files("cases")) == 9 and len(_files("holdout")) == 12
    for case in cases:
        spec, sales = case["spec"], case["expected"]["sales"]
        gaps = (spec.get("payment_gap") or {}).get("days_ago", [])
        if 0 in gaps:
            assert sales == {"status": "unknown", "unknown_reason": "payment_gap"}
            continue
        left = 7 - len([d for d in gaps if d > 0])  # 공백이 아닌 지난 날
        if left < 3:  # 기준선 부족 (최소 3일)
            assert sales == {"status": "unknown", "unknown_reason": "baseline_short",
                             "baseline_days": left}  # fmt: skip
            continue
        injection = spec["injection"]
        factor = float(injection.get("factor", 1.0)) if injection["type"] != "none" else 1.0
        lift = factor * int((spec.get("basket") or {}).get("qty_factor", 1))
        want = "up" if lift > 1 else "down" if lift < 1 else "none"
        assert sales["status"] == "known" and sales["direction"] == want
        assert sales["direct_attributed"] == bool(
            case["input"]["campaign_id"] and spec.get("campaign")
        )
        # 공백 날은 센 값의 기준선에서 빠진다
        assert all(str(d) not in case["counted"]["gross_past_days"] for d in gaps)


def test_scorer_counts_right_wrong_and_missing() -> None:
    score = _load("score")
    cases = list(_files("cases").values())
    perfect = {}
    for c in cases:
        want, counted = c["expected"]["sales"], c["counted"]
        if want["status"] == "unknown":
            reason = (
                "기준선 부족 (2일, 최소 3일)"
                if want["unknown_reason"] == "baseline_short"
                else "결제 기록 공백 (5칸)"
            )
            perfect[c["id"]] = score.Answer.model_validate(
                {"sales": {"status": "unknown", "unknown_reason": reason}}
            )
        else:
            perfect[c["id"]] = score.Answer.model_validate({"sales": {
                "status": "known",
                "direction": "uncertain" if want["direction"] == "none" else want["direction"],
                "incremental_gross_krw": round(counted["gross_excess"]),
                "direct_attributed_krw": counted["direct_attributed"],
            }})  # fmt: skip
    got = score.score(cases, perfect)["metrics"]
    assert got["campaign_revenue.direction_rate"] == 1.0
    assert got["campaign_revenue.gap_unknown_rate"] == 1.0
    assert got["campaign_revenue.short_baseline_unknown_rate"] == 1.0
    assert got["campaign_revenue.direct_match"] == 1.0
    assert got["campaign_revenue.excess_error"] < 0.001
    bad = dict(perfect)
    gap_id = next(c["id"] for c in cases if c["expected"]["sales"]["status"] == "unknown")
    bad[gap_id] = score.Answer.model_validate({"sales": {"status": "known", "direction": "up"}})
    up_id = next(c["id"] for c in cases if c["expected"]["sales"].get("direction") == "up")
    bad[up_id] = None  # 판정 실패 = missing
    worse = score.score(cases, bad)
    assert worse["metrics"]["campaign_revenue.gap_unknown_rate"] == 0.0
    assert worse["metrics"]["campaign_revenue.direction_rate"] < 1.0
    assert {w["got"] for w in worse["wrong"]} >= {"known", "missing"}


@pytest.mark.eval
def test_regenerated_cases_match_the_committed_files(tmp_path: Path) -> None:
    make = _load("make_cases")
    make.write(tmp_path / "cases", make.build_cases())
    make.write(tmp_path / "holdout", make.build_holdout())
    for folder in ("cases", "holdout"):
        rebuilt = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((tmp_path / folder).glob("*.json"))}  # fmt: skip
        assert rebuilt == _files(folder)
