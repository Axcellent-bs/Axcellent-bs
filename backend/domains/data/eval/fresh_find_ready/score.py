"""새 숏츠 찾기 준비 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표:
- `fresh_find_ready.no_spend_when_unclassifiable`: LLM 장부가 판정을 막는 케이스 중 찾기 0번·
  YouTube 0단위·찾기 시각 없음이 모두 맞은 비율
- `fresh_find_ready.finds_when_classifiable`: 막지 않는 케이스 중 찾기 수·단위·찾기 시각 수·
  추적 수가 모두 정답과 같은 비율
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "fresh_find_ready.no_spend_when_unclassifiable",
    "fresh_find_ready.finds_when_classifiable",
)
KEYS = ("searches", "units", "finds_recorded", "added")


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}
    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        metric = METRICS[1] if want["searches"] else METRICS[0]
        ok = bool(got) and all(got.get(k) == want[k] for k in KEYS)
        hits[metric][0] += ok
        hits[metric][1] += 1
    return {m: (round(h / n, 4) if n else 0.0) for m, (h, n) in hits.items()}
