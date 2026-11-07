"""새 숏츠 찾기 준비 평가 케이스: LLM 장부가 형식 판정을 막을 때 찾기(검색 100단위)를 부르는지.

    cd backend
    uv run python domains/data/eval/fresh_find_ready/make_cases.py

규칙 (이 파일이 직접 계산, skill과 따로 적음):
- 찾은 영상은 형식을 판정(LLM)해야 추적한다. LLM 장부(한국 날짜)는 하루 상한(호출 200·토큰
  40만)에 닿았거나, 사용 비율(호출·토큰 중 큰 쪽)이 80% 이상이면 브리프가 아닌 일을 막는다
- 막힐 때: 찾기 0번, YouTube 0단위, 찾기 시각을 남기지 않음 (LLM 하루가 바뀐 뒤 바로 다시)
- 막히지 않을 때: 형식마다 찾기 1번(검색 100 + 결과 조회 1), 60초 이하 새 영상만 추적
- 가짜 세상: 형식 2개, 형식마다 검색 결과 3개(30초·59초·90초) → 추적 4개, 202단위

2026-11-08 00:37 LLM 사용 70%에서 04:30 찾기 전에 80%를 넘을 수 있어 만든 케이스다.
"""

from __future__ import annotations

import json
import sys

CALL_CAP, TOKEN_CAP, RESERVE = 200, 400_000, 0.8
FORMATS = ("정보·팁", "챌린지")
SECONDS = (30, 59, 90)
MAX_SECONDS, SEARCH_UNITS = 60, 100
USAGE = {  # 이름: (오늘 LLM 호출, 토큰)
    "llm_low": (10, 100_000),
    "llm_just_below": (50, 319_999),
    "llm_at_reserve_tokens": (50, 320_000),
    "llm_at_reserve_calls": (160, 0),
    "llm_cap_reached": (200, 400_000),
}


def _allowed(calls: int, tokens: int) -> bool:
    reached = calls >= CALL_CAP or tokens >= TOKEN_CAP
    return not reached and max(calls / CALL_CAP, tokens / TOKEN_CAP) < RESERVE


def cases() -> list[dict]:
    out = []
    for name, (calls, tokens) in USAGE.items():
        ok = _allowed(calls, tokens)
        short = sum(1 for s in SECONDS if s <= MAX_SECONDS)
        out.append({
            "name": name,
            "llm_calls": calls,
            "llm_tokens": tokens,
            "formats": list(FORMATS),
            "seconds": list(SECONDS),
            "expected": {
                "searches": len(FORMATS) if ok else 0,
                "units": len(FORMATS) * (SEARCH_UNITS + 1) if ok else 0,
                "finds_recorded": len(FORMATS) if ok else 0,
                "added": len(FORMATS) * short if ok else 0,
            },
        })  # fmt: skip
    return out


if __name__ == "__main__":
    json.dump(cases(), sys.stdout, ensure_ascii=False, indent=1)
