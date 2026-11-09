"""여러 날 캠페인 4주 기준선 평가 정의(`eval/campaign/multiday4/`)의 약속.

- 정답(방향)은 주입 조건에서, 시작 전 기록 안의 같은 요일 구간이 3개보다 적으면 short_history
- 기대(`counted.orders_baseline`)는 쓸 수 있는 구간(7·14·21·28일 전) 평균
- 다시 만들면 커밋된 파일과 같다 (느려서 `eval` 표시)
holdout은 사람이 열지 않는다. 기계로 조건 일치만 본다.
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
    sys.path.insert(0, str(CAMPAIGN))
    spec = importlib.util.spec_from_file_location("multiday4_make", CAMPAIGN / "make_multiday4.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((CAMPAIGN / "multiday4" / folder).glob("*.json"))
    }


def test_answers_and_baseline_weeks_follow_the_definition() -> None:
    cases = [*_files("cases").values(), *_files("holdout").values()]
    assert len(_files("cases")) == 8 and len(_files("holdout")) == 10
    for case in cases:
        history = case["spec"]["traffic"]["history_days"]
        weeks = [k for k in (7, 14, 21, 28) if k <= history]
        assert case["counted"]["baseline_weeks"] == weeks
        if len(weeks) < 3:
            assert case["expected"]["orders"] == {"status": "unknown",
                                                  "unknown_reason": "short_history"}  # fmt: skip
            assert case["counted"]["orders_baseline"] is None
            continue
        injection = case["spec"]["injection"]
        factor = float(injection.get("factor", 1.0)) if injection["type"] != "none" else 1.0
        want = "up" if factor > 1 else "down" if factor < 1 else "none"
        assert case["expected"]["orders"]["direction"] == want
        assert case["counted"]["orders_baseline"] is not None


@pytest.mark.eval
def test_regenerated_cases_match_the_committed_files(tmp_path: Path) -> None:
    make = _make()
    for folder, items in (("cases", make.build_cases()), ("holdout", make.build_holdout())):
        make.write(tmp_path / folder, items)
        rebuilt = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((tmp_path / folder).glob("*.json"))}  # fmt: skip
        assert rebuilt == _files(folder)
