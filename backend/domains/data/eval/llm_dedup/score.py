"""LLM 같은 요청 중복 계산 막기 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `llm_dedup.call_count_accuracy`: 가짜 LLM 서버가 받은 HTTP 요청 수가 정답과 같고, 만료 케이스는
  만료 전에 부르지 않은(기다린 시간 ≥ min_wait) 케이스 비율
- `llm_dedup.answer_accuracy`: 요청마다 맞는 답(성공 수·실패 수)이 정답과 같은 케이스 비율
- `llm_dedup.skill_named_rate`: 장부(skill별 사용량)에 남은 skill 이름이 정답과 같은 케이스 비율
실행 결과가 없으면(None, 모듈 없음) 그 케이스는 틀린 것으로 센다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "llm_dedup.call_count_accuracy",
    "llm_dedup.answer_accuracy",
    "llm_dedup.skill_named_rate",
)


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        if case["kind"] == "calls":
            waited = got.get("max_wait")
            on_time = "min_wait" not in want or (waited is not None and waited >= want["min_wait"])
            count(METRICS[0], bool(got) and got.get("calls") == want["calls"] and on_time)
            count(METRICS[1], bool(got) and got.get("ok") == want["ok"]
                  and got.get("errors") == want["errors"])  # fmt: skip
        elif case["kind"] == "skill":
            count(METRICS[2], bool(got) and got.get("skills") == want["skills"])
    return {m: (round(h / n, 4) if n else 0.0) for m, (h, n) in hits.items()}
