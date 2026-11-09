"""데이터분석 AI 직접 대화 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `data_chat.bundle_accuracy`: 질문 20개의 고른 묶음이 정답과 같은 비율
- `data_chat.number_check_accuracy`: 답 숫자 ⊆ facts 검사(통과·고정 문장)가 정답과 같은 비율
- `data_chat.action_accuracy`: 명령은 거절하고 LLM 0회, 질문은 거절하지 않은 비율
- `data_chat.no_compute_rate`: 저장 결과가 없을 때 고정 문장이고 계산·LLM·YouTube 호출 0회인 비율
- `data_chat.limit_accuracy`: 재료 4,000자·지난 대화 6개·하루 30회/80,000토큰·80%/100%·
  장부 이름이 맞은 비율
- `data_chat.privacy_rate`: 개인정보 결과가 묶음·재료에 없는 비율
실행 결과가 없으면(None, 모듈 없음) 그 케이스는 틀린 것으로 센다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "data_chat.bundle_accuracy",
    "data_chat.number_check_accuracy",
    "data_chat.action_accuracy",
    "data_chat.no_compute_rate",
    "data_chat.limit_accuracy",
    "data_chat.privacy_rate",
)
KIND_METRIC = {
    "pick": METRICS[0],
    "number": METRICS[1],
    "action": METRICS[2],
    "empty": METRICS[3],
    "material": METRICS[4],
    "history": METRICS[4],
    "limit": METRICS[4],
    "ledger": METRICS[4],
    "privacy": METRICS[5],
}


def _ok(case: dict, got: Any) -> bool:
    want, kind = case["expected"], case["kind"]
    if got is None:
        return False
    if kind in ("pick", "number", "limit", "ledger"):
        return got == want
    if kind == "action":
        if got.get("refused") != want["refused"]:
            return False
        return want["llm_calls"] is None or got.get("llm_calls") == want["llm_calls"]
    if kind == "empty":
        return (str(got.get("reply", "")).startswith(want["prefix"])
                and got.get("llm_calls") == 0 and got.get("computed") == 0)  # fmt: skip
    if kind == "material":
        return bool(got.get("basis_ok")) and 0 < got.get("chars", 10**9) <= want["max_chars"]
    if kind == "history":
        return 0 < got.get("history", 10**9) <= want["max_history"]
    if kind == "privacy":
        return got.get("personal_bundles") == want["personal_bundles"]
    return False


def score(cases: list[dict], answers: Mapping[str, Any]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}
    for case in cases:
        metric = KIND_METRIC[case["kind"]]
        hits[metric][0] += _ok(case, answers.get(case["name"]))
        hits[metric][1] += 1
    return {m: (round(h / n, 4) if n else 0.0) for m, (h, n) in hits.items()}
