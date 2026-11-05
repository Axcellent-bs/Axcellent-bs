"""campaign_effect 상품 기준 평가 정의(`eval/campaign_products/`)의 약속을 지키는지 본다 (#646).

- 정답(방향·매칭·unknown)은 주입 조건과 카탈로그 시드에서 나온다
- 채점기: 맞는 답은 1.0·오차 0, 다른 상품 증가를 up으로 답하면 틀림, 판정 실패는 missing
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

PRODUCTS = Path(__file__).resolve().parents[1] / "eval" / "campaign_products"
PARTS = ("orders", "units", "gross")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"campaign_products_{name}", PRODUCTS / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((PRODUCTS / folder).glob("*.json"))
    }


def test_answers_come_from_the_injection_and_the_catalog() -> None:
    make = _load("make_cases")
    assert len(_files("cases")) == 9 and len(_files("holdout")) == 12
    for case in [*_files("cases").values(), *_files("holdout").values()]:
        spec, want = case["spec"], case["expected"]["products"]
        assert (want["matched"], want["unmatched"]) == make.match(case["input"]["products"])
        gaps = (spec.get("payment_gap") or {}).get("days_ago", [])
        if not want["matched"]:
            assert want["status"] == "unknown" and want["unknown_reason"] == "no_match"
            continue
        if 0 in gaps:
            assert want["status"] == "unknown" and want["unknown_reason"] == "payment_gap"
            continue
        left = 7 - len([d for d in gaps if d > 0])
        if left < 3:
            assert want["unknown_reason"] == "baseline_short" and want["baseline_days"] == left
            continue
        injection = spec["injection"]
        factor = float(injection.get("factor", 1.0)) if injection["type"] != "none" else 1.0
        boost = spec.get("product_boost")
        lift = factor * (boost["factor"] if boost and boost["product_id"] in want["matched"] else 1)
        direction = "up" if lift > 1 else "down" if lift < 1 else "none"
        assert want["status"] == "known" and all(want[p] == direction for p in PARTS)
        assert all(str(d) not in case["counted"]["past_days"] for d in gaps)


def test_partial_names_are_not_guessed() -> None:
    make = _load("make_cases")
    assert make.match(["무지 노트", "MUJI"]) == ([], ["무지 노트", "MUJI"])
    assert make.match([" 무지노트 3권세트 ", "prd_0004"]) == (["prd_0004"], [])


def _perfect(score: ModuleType, cases: list[dict]) -> dict:
    out = {}
    words = {"no_match": "맞는 상품 없음", "payment_gap": "결제 기록 공백 (5칸)",
             "baseline_short": "기준선 부족 (2일, 최소 3일)"}  # fmt: skip
    for c in cases:
        want, counted = c["expected"]["products"], c["counted"]
        body = {"matched": want["matched"], "unmatched": want["unmatched"]}
        if want["status"] == "unknown":
            body |= {"status": "unknown", "unknown_reason": words[want["unknown_reason"]]}
        else:
            body["status"] = "known"
            for p in PARTS:
                d = "uncertain" if want[p] == "none" else want[p]
                body[p] = {"direction": d, "incremental": counted[f"{p}_excess"]}
        out[c["id"]] = score.Answer.model_validate({"products": body})
    return out


def test_scorer_counts_right_wrong_and_missing() -> None:
    score = _load("score")
    cases = list(_files("cases").values())
    perfect = _perfect(score, cases)
    got = score.score(cases, perfect)["metrics"]
    names = (
        "direction_rate",
        "other_product_none_rate",
        "match_rate",
        "no_match_unknown_rate",
        "gap_unknown_rate",
        "short_baseline_unknown_rate",
    )
    for name in names:
        assert got[f"campaign_products.{name}"] == 1.0, name
    assert got["campaign_products.excess_error"] < 0.001
    bad = dict(perfect)
    other = "other_product_up_2h_14h"  # 다른 상품 증가를 이 상품 성과로 셈
    body = perfect[other].products.model_dump()
    body["orders"] = {"direction": "up", "incremental": 30}
    bad[other] = score.Answer.model_validate({"products": body})
    guess = "name_unmatched_2h_14h"  # 일부만 같은 이름을 추측해 맞춤
    bad[guess] = score.Answer.model_validate({"products": {
        "status": "known", "matched": ["prd_0004"], "unmatched": ["없는 상품"]}})  # fmt: skip
    bad["product_up_x3_2h_14h"] = None  # 판정 실패 = missing
    worse = score.score(cases, bad)
    assert worse["metrics"]["campaign_products.other_product_none_rate"] == 0.0
    assert worse["metrics"]["campaign_products.no_match_unknown_rate"] == 0.0
    assert worse["metrics"]["campaign_products.match_rate"] < 1.0
    assert worse["metrics"]["campaign_products.direction_rate"] < 1.0
    assert {w["got"] for w in worse["wrong"]} >= {"up", "known", "missing"}


@pytest.mark.eval
def test_regenerated_cases_match_the_committed_files(tmp_path: Path) -> None:
    make = _load("make_cases")
    make.write(tmp_path / "cases", make.build_cases())
    make.write(tmp_path / "holdout", make.build_holdout())
    for folder in ("cases", "holdout"):
        rebuilt = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((tmp_path / folder).glob("*.json"))}  # fmt: skip
        assert rebuilt == _files(folder)
