"""인사이트 브리프 기준·홍보 영상 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `insight_gate.held_back_accuracy`: 근거가 없는 트렌드 인사이트
  (가격만·검색 줄어듦·팔지 않는 상품군)를 넘기지 않고 정답 이유를 남긴 비율
- `insight_gate.promo_accuracy`: 숏츠 형식 케이스에서 넘김 여부·근거 숫자·이유가 정답과 같은 비율
  (넘긴 형식은 LLM 없이 고정 틀 아이디어 + fallback 표시까지)
실행 결과가 없으면(None, 모듈 없음) 그 케이스는 틀린 것으로 센다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = ("insight_gate.held_back_accuracy", "insight_gate.promo_accuracy")
BY = {"trend": METRICS[0], "promo": METRICS[1]}


def score(cases: list[dict], answers: Mapping[str, Any]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}
    for case in cases:
        got = answers.get(case["name"])
        ok = isinstance(got, dict) and all(got.get(k) == v for k, v in case["expected"].items())
        h = hits[BY[case["kind"]]]
        h[0] += int(ok)
        h[1] += 1
    return {m: round(a / b, 4) if b else 0.0 for m, (a, b) in hits.items()}
