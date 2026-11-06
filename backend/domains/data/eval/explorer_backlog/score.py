"""영상 탐색 밀린 길이 확인·분류 대기 채점기.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `explorer_backlog.reread_cap_kept`: 실행마다 다시 읽기 호출 수가 정답(장부 날짜마다 50유닛)과
  같은 비율
- `explorer_backlog.short_first_rate`: 1유닛(50개)으로 읽은 영상 중 정답(숏 후보·최근 순 앞 50개)에
  든 비율
- `explorer_backlog.batch_isolation`: 묶음 분류에서 영상마다 자기 결과를 받은 비율
- `explorer_backlog.shorts_first_near_cap`: 분류에 물은 영상이 정답(숏츠 먼저, 상한 가까우면 숏츠만)
  과 같은 케이스 비율
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "explorer_backlog.reread_cap_kept",
    "explorer_backlog.short_first_rate",
    "explorer_backlog.batch_isolation",
    "explorer_backlog.shorts_first_near_cap",
)


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        if case["kind"] == "reread_cap":
            calls = list(got.get("calls") or [])
            for n, w in enumerate(want["calls"]):
                count(METRICS[0], n < len(calls) and calls[n] == w)
        elif case["kind"] == "reread_order":
            read = set(got.get("read") or [])
            for vid in want["read"]:
                count(METRICS[1], vid in read)
        elif case["kind"] == "batch":
            cats = got.get("categories") or {}
            for vid, cat in want.items():
                count(METRICS[2], cats.get(vid) == cat)
        else:
            count(METRICS[3], sorted(got.get("asked") or []) == want["asked"])
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}
