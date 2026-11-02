"""trend_scan 평가 케이스(`eval/trend/`)의 약속을 지키는지 본다. 네이버를 부르지 않는다.

- 케이스·holdout은 저장된 시계열과 seed만으로 다시 만들어진다(커밋된 파일과 같다)
- 정답은 주입 조건에서 나온다(급상승 날짜 = 주입한 날짜)
- 저장된 시계열에는 날짜와 값만 있다(네이버 원본 응답을 커밋하지 않는다)
- 케이스 생성은 네트워크 코드를 import하지 않는다
holdout은 파일 내용을 사람이 열지 않는다. 여기서는 다시 만든 결과와 같은지·개수만 기계로 본다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

TREND = Path(__file__).resolve().parents[1] / "eval" / "trend"


def _make_cases() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trend_make_cases", TREND / "make_cases.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files(folder: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((TREND / folder).glob("*.json"))
    }


def test_cases_are_rebuilt_from_saved_series_and_seed() -> None:
    make = _make_cases()
    assert {c["id"]: c for c in make.build_cases()} == _files("cases")


def test_holdout_is_rebuilt_and_large_enough() -> None:
    make = _make_cases()
    held = _files("holdout")
    assert {c["id"]: c for c in make.build_holdout()} == held
    assert len(held) >= 20
    assert {c["holdout"]["seed"] for c in held.values()} == {make.HOLDOUT_SEED}
    assert make.HOLDOUT_SEED != make.CASES_SEED


def test_case_generation_does_not_touch_network() -> None:
    source = (TREND / "make_cases.py").read_text(encoding="utf-8")
    for name in ("httpx", "urllib", "requests", "naver_datalab", "data_agent", "socket"):
        assert name not in source


def test_saved_series_hold_only_dates_and_values() -> None:
    for path in sorted((TREND / "series").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"source", "fetched", "time_unit", "start_date", "end_date", "groups"}
        for body in data["groups"].values():
            assert set(body) == {"keywords", "points"}
            for point in body["points"]:
                day, value = point  # 정확히 [날짜, 값]
                date.fromisoformat(day)
                assert isinstance(value, int | float) and value >= 0
        text = path.read_text(encoding="utf-8")
        for raw_field in ('"results"', '"title"', '"period"', '"ratio"', '"keywordGroups"'):
            assert raw_field not in text


def test_series_look_like_naver_responses() -> None:
    for case in _files("cases").values():
        values = [v for points in case["series"].values() for _, v in points]
        if values:  # 요청 안 최댓값 = 100 (주입 뒤 다시 맞춤), 소수 다섯째 자리
            assert max(values) == 100.0
            assert all(round(v, 5) == v for v in values)


def test_answers_come_from_injection_conditions() -> None:
    for case in _files("cases").values():
        window_from = date.fromisoformat(case["window"]["from"])
        window_to = date.fromisoformat(case["window"]["to"])
        injection = case["injection"]
        for expected in case["expected"].values():
            if case["kind"] in ("injected", "joint") and expected["status"] == "rising":
                start = date.fromisoformat(injection["start"])
                days = [start + timedelta(days=i) for i in range(injection["days"])]
                assert expected["multiplier"] == injection["multiplier"]
                assert [date.fromisoformat(d) for d in expected["rising_dates"]] == days
                assert all(window_from <= d <= window_to for d in days)
            if case["kind"] == "past_spike":
                start = date.fromisoformat(injection["start"])
                assert start + timedelta(days=injection["days"] - 1) < window_from
                assert expected["status"] == "normal"
            if expected["status"] == "unknown":
                assert expected["unknown_reason"]


def test_case_kinds_cover_the_plan() -> None:
    kinds: dict[str, int] = {}
    for case in _files("cases").values():
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    assert kinds["injected"] >= 9
    assert kinds["none"] >= 1 and kinds["seasonal"] >= 1
    assert kinds["unknown"] >= 2
    assert 2 <= kinds["real_event"] <= 3
    events = [c for c in _files("cases").values() if c["kind"] == "real_event"]
    assert all("http" in c["memo"] for c in events)  # 날짜 출처 링크
