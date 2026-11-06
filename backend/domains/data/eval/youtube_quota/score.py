"""YouTube 할당량 정리 채점기.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `quota.reserve_guaranteed`: 다른 기능이 6,800단위 안에서 멈추고(검색 수가 정답과 같고 단위가
  상한 이하), 그 뒤에도 새 숏츠 추적이 자기 몫(1,200단위)만큼 검색했는지. 맞은 항목 비율
- `quota.fill_cap_respected`: 보충 검색 케이스마다 실제 검색 수가 정답(카테고리당 하루 2번, 실패도
  셈, 성공은 6시간 재사용)과 같은지. 맞은 케이스 비율
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = ("quota.reserve_guaranteed", "quota.fill_cap_respected")


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        if case["kind"] == "reserve":
            count(METRICS[0], got.get("others_searches") == want["others_searches"])
            units = got.get("others_units")
            count(METRICS[0], units is not None and units <= want["others_units_max"])
            count(METRICS[0], got.get("fresh_searches") == want["fresh_searches"])
        else:
            count(METRICS[1], got.get("calls") == want["max_calls"])
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}
