"""campaign_effect 유입경로 평가 정의(`eval/campaign_attribution/`)의 약속 (#648).

- 정답은 심은 조건과 생성기 기록에서 나온다 (유입 표시 없음 = unknown, 다른 캠페인은 안 셈)
- 채점기: 맞는 답은 1.0·오차 0, 다른 캠페인을 세거나 표시 없음을 0건으로 답하면 틀림, 실패는 missing
- 다시 만들면 커밋된 파일과 같다 (`eval` 표시)
이 PR에서는 지표를 관찰만 한다(관문 없음). 관문은 skill PR에서 켠다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ATTR = Path(__file__).resolve().parents[1] / "eval" / "campaign_attribution"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"attribution_{name}", ATTR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((ATTR / folder).glob("*.json"))}  # fmt: skip


def test_answers_come_from_the_planted_tags() -> None:
    assert len(_files("cases")) == 6 and len(_files("holdout")) == 10
    for case in [*_files("cases").values(), *_files("holdout").values()]:
        tags = (case["spec"].get("attribution") or {}).get("tags", [])
        counted, want = case["counted"], case["expected"]
        me = case["input"]["campaign_id"]
        if counted["tagged_orders"] == 0:
            assert want["attribution"] == {"status": "unknown", "unknown_reason": "no_tags"}
            continue
        assert tags and want["attribution"]["status"] == "known"
        if not any(t["campaign_id"] == me for t in tags):  # 다른 캠페인만: 0건
            assert counted["orders"] == 0 and counted["other_campaign_orders"] > 0
        assert counted["orders"] == sum(r["orders"] for r in counted["by_source"].values())
        assert set(counted["by_source"]) <= {t["source"] for t in tags if t["campaign_id"] == me}


def _perfect(score: ModuleType, cases: list[dict]) -> dict:
    out = {}
    for c in cases:
        counted, want = c["counted"], c["expected"]
        if want["attribution"]["status"] == "unknown":
            body: dict = {"attribution": {"status": "unknown", "unknown_reason": "유입 기록 없음"}}
        else:
            body = {"attribution": {"status": "known", "orders": counted["orders"],
                                    "gross_krw": counted["gross_krw"],
                                    "by_source": counted["by_source"],
                                    "tagged_share": counted["tagged_share"]}}  # fmt: skip
        if "overlap" in want:
            body["overlap"] = (
                {"status": "unknown", "unknown_reason": "유입 기록 없음"}
                if want["overlap"]["status"] == "unknown"
                else {"status": "known", **counted["overlap"]}
            )  # fmt: skip
        out[c["id"]] = score.Answer.model_validate(body)
    return out


def test_scorer_counts_right_wrong_and_missing() -> None:
    score = _load("score")
    cases = list(_files("cases").values())
    perfect = _perfect(score, cases)
    got = score.score(cases, perfect)["metrics"]
    for name in ("count_match", "no_tag_unknown_rate", "other_campaign_excluded_rate",
                 "overlap_match"):  # fmt: skip
        assert got[f"campaign_attribution.{name}"] == 1.0, name
    assert got["campaign_attribution.share_error"] == 0.0
    bad = dict(perfect)
    other = "other_campaign_only_2h_14h"  # 다른 캠페인 주문을 셈
    bad[other] = score.Answer.model_validate({"attribution": {
        "status": "known", "orders": 45, "gross_krw": 1, "tagged_share": 0.3}})  # fmt: skip
    bad["no_tags_2h_10h"] = score.Answer.model_validate({"attribution": {  # 0으로 채움
        "status": "known", "orders": 0, "gross_krw": 0}})  # fmt: skip
    bad["tagged_two_sources_2h_14h"] = None  # 판정 실패
    worse = score.score(cases, bad)["metrics"]
    assert worse["campaign_attribution.other_campaign_excluded_rate"] < 1.0
    assert worse["campaign_attribution.no_tag_unknown_rate"] < 1.0
    assert worse["campaign_attribution.count_match"] < 1.0


@pytest.mark.eval
def test_regenerated_cases_match_the_committed_files(tmp_path: Path) -> None:
    make = _load("make_cases")
    make.write(tmp_path / "cases", make.build_cases())
    make.write(tmp_path / "holdout", make.build_holdout())
    for folder in ("cases", "holdout"):
        rebuilt = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((tmp_path / folder).glob("*.json"))}  # fmt: skip
        assert rebuilt == _files(folder)
