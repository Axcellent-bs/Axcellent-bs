"""LLM 같은 요청 중복 계산 막기 평가 케이스 생성기.

    cd backend
    uv run python domains/data/eval/llm_dedup/make_cases.py

배경: 11-08 0시에 화면 요청(trend_popular·trend_insight_brief·promo_concepts)과 수집기의 미리 계산이
같은 분류 요청을 동시에 LLM에 보냈다 (같은 입력 토큰 요청 2~3번, 17회 · 83,886토큰 = 그날의 26%).

규칙 (이 파일이 직접 적음, 코드와 따로):
- 같은 요청(모델·응답 형식·프롬프트·데이터 블록이 같음)이 동시에 오면 LLM은 1번만 부르고,
  기다린 요청은 그 답을 같이 받는다. 다른 요청은 묶지 않는다
- 먼저 부른 쪽이 실패하면 그 요청만 실패하고, 기다리던 쪽 하나가 대신 1번 부른다 (나머지는 그 답)
- "계산 중" 표시를 남긴 프로세스가 죽으면 표시가 만료될 때까지 기다린 뒤 대신 부른다
  (만료 전에 다시 부르지 않는다)
- 서버와 수집기처럼 서로 다른 프로세스여도 같은 요청은 1번 (같은 장부 파일을 같이 쓴다)
- 수집기의 새 숏츠 형식 분류는 장부에 skill "fresh_shorts"로 남는다 ("unknown" 아님)

각 케이스는 `worker.py`(별도 프로세스)로 실제 `TimedLLMClient` + 가짜 LLM 서버를 돌린다.
"""

from __future__ import annotations

import json
import sys

ANSWER = 7  # 가짜 LLM이 돌려주는 값 (Answer.x)


def _ask(prompt: str, delay: float = 0.0) -> dict:
    return {"prompt": prompt, "delay": delay}


def cases() -> list[dict]:
    same = "분류: 같은 영상 묶음"
    return [
        {
            "name": "same_three_at_once",
            "kind": "calls",
            "why": "같은 요청 3개 동시 → LLM 1회, 셋 다 같은 답",
            "workers": [{"requests": [_ask(same), _ask(same), _ask(same)], "latency": 0.5}],
            "expected": {"calls": 1, "ok": 3, "errors": 0},
        },
        {
            "name": "different_two_at_once",
            "kind": "calls",
            "why": "다른 요청은 묶지 않는다 → LLM 2회",
            "workers": [
                {"requests": [_ask("분류: 묶음 A"), _ask("분류: 묶음 B")], "latency": 0.5}
            ],  # fmt: skip
            "expected": {"calls": 2, "ok": 2, "errors": 0},
        },
        {
            "name": "owner_fails_waiter_takes_over",
            "kind": "calls",
            "why": "먼저 부른 쪽이 500 → 그 요청만 실패, 기다리던 쪽 하나가 대신 1번 (총 2회)",
            "workers": [
                {
                    "requests": [_ask(same), _ask(same, 0.1), _ask(same, 0.1)],
                    "latency": 0.5,
                    "fail_first": True,
                }
            ],  # fmt: skip
            "expected": {"calls": 2, "ok": 2, "errors": 1},
        },
        {
            "name": "crashed_owner_expires",
            "kind": "calls",
            "why": "표시를 남긴 프로세스가 죽음 → 만료(8초)까지 기다린 뒤 대신 1번",
            "lease": 8,
            "crash_first": True,
            "workers": [{"requests": [_ask(same)], "latency": 0.2}],
            "expected": {"calls": 1, "ok": 1, "errors": 0, "min_wait": 1.0},
        },
        {
            "name": "server_and_collector",
            "kind": "calls",
            "why": "서버·수집기(서로 다른 프로세스)가 같은 요청 동시 → LLM 1회",
            "together": True,
            "workers": [
                {"requests": [_ask(same)], "latency": 1.0, "skill": "trend_popular"},
                {"requests": [_ask(same)], "latency": 1.0, "skill": "trend_popular@precompute1"},
            ],
            "expected": {"calls": 1, "ok": 2, "errors": 0},
        },
        {
            "name": "fresh_shorts_named",
            "kind": "skill",
            "why": "수집기 새 숏츠 형식 분류 → 장부 skill fresh_shorts",
            "workers": [{"mode": "fresh", "latency": 0.0}],
            "expected": {"skills": ["fresh_shorts"]},
        },
    ]


if __name__ == "__main__":
    json.dump(cases(), sys.stdout, ensure_ascii=False, indent=2)
    print()
