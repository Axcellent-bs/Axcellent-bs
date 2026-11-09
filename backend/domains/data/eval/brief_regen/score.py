"""인사이트 브리프 다시 추천 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `brief_regen.gate_accuracy`: 막는 조건(하루 3번·1시간·LLM 70%·no_new_data)과
  횟수 차감(막힘·실패·자동 판은 안 셈), 잠금 만료 뒤 재시작이 정답과 같은 비율
- `brief_regen.single_build_rate`: 같은 프로세스 두 번·서로 다른 두 프로세스가 동시에 눌러도
  판이 1개인 비율
- `brief_regen.version_accuracy`: 판 보존·기본 최신·규칙별 분리·insight_id 유지·briefs 표 최신·
  diff가 맞은 비율
실행 결과가 없으면(None, 모듈 없음) 그 케이스는 틀린 것으로 센다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "brief_regen.gate_accuracy",
    "brief_regen.single_build_rate",
    "brief_regen.version_accuracy",
)
BY = {"gate": METRICS[0], "single": METRICS[1], "version": METRICS[2]}


def score(cases: list[dict], answers: Mapping[str, Any]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}
    for case in cases:
        got = answers.get(case["name"])
        ok = isinstance(got, dict) and all(got.get(k) == v for k, v in case["expected"].items())
        metric = BY[case["metric"]]
        hits[metric][0] += ok
        hits[metric][1] += 1
    return {m: (round(h / n, 4) if n else 0.0) for m, (h, n) in hits.items()}
