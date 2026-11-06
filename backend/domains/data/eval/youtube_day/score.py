"""YouTube 장부 하루 경계·전체 상한 채점기.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `quota_day.boundary_accuracy`: 서머타임이 아닌 날 경계 앞뒤 시각의 장부 날짜·다음 초기화 문구가
  정답과 같은 비율
- `quota_day.dst_accuracy`: 서머타임 전환일(끝·시작) 경계 앞뒤의 날짜·문구가 맞는 비율
- `quota_day.cap_kept_rate`: 상한 케이스에서 성공한 호출 중 합계가 8,000을 넘기지 않은 비율과
  새 숏츠 검색 수·1단위 호출 수가 정답과 같은지 (합계 초과가 한 번이라도 있으면 1.0이 아님)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = ("quota_day.boundary_accuracy", "quota_day.dst_accuracy", "quota_day.cap_kept_rate")


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        if case["kind"] == "day":
            metric = METRICS[1] if case["dst"] else METRICS[0]
            count(metric, got.get("day") == want["day"])
            count(metric, got.get("label") == want["label"])
        else:
            totals = got.get("totals")
            count(METRICS[2], totals is not None and all(t <= case["cap"] for t in totals))
            count(METRICS[2], got.get("fresh_searches") == want["fresh_searches"])
            count(METRICS[2], got.get("unit_calls") == want["unit_calls"])
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}


def over_cap(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> int:
    """합계가 상한을 넘긴 호출 수 (보고용)."""
    n = 0
    for case in cases:
        if case["kind"] == "cap":
            got = answers.get(case["name"]) or {}
            n += sum(1 for t in got.get("totals") or [] if t > case["cap"])
    return n
