"""인사이트 브리프 "다시 추천"·판 저장 평가 케이스 생성기.

    cd backend
    uv run python domains/data/eval/brief_regen/make_cases.py

규칙 (이 파일이 직접 적음, 코드와 따로):
- 다시 추천은 하루 3번(한국 0시 초기화). 자동 판(0시 이후 첫 브리프)은 횟수에 세지 않는다
- 마지막 판(자동 포함) 뒤 1시간 안이면 막는다 ("HH:MM 이후에 다시 추천할 수 있어요")
- LLM 오늘 사용 70% 이상이면 막는다
- 이미 만드는 중이면(누가 눌렀든, 자동 포함) 막는다. 동시에 두 번 눌러도, 서버·수집기 두 프로세스가
  동시에 만들어도 판은 1개만 생긴다. "만드는 중" 잠금은 sqlite에 두고 10분 뒤 만료
- 마지막 판과 감지 스냅샷이 같으면 no_new_data
- 막힌 경우·실패는 횟수를 차감하지 않고, 실패하면 이전 판이 그대로 최신이다
- 같은 날 판은 지우지 않는다. 기본은 최신 판. 판 목록은 (날짜, 현재 규칙 버전) 기준
- 같은 날 같은 주제는 판이 바뀌어도 같은 insight_id
- 기존 briefs 표에는 그날 최신 판이 들어가 대화(read_insight)가 최신 판을 읽는다
- diff: 이전 판 대비 새 주제·빠진 주제 이름과 개수
"""

from __future__ import annotations

import json
import sys

CASES = [
    # (이름, 지표, 설명)
    ("daily_limit", "gate", "다시 추천 3번 뒤 4번째는 daily_limit, 횟수 3 그대로"),
    ("cooldown_after_auto", "gate", "자동 판 30분 뒤 누름 → cooldown, 문장에 다시 가능한 HH:MM"),
    ("llm_70", "gate", "LLM 오늘 72% → llm_budget"),
    ("no_new_data", "gate", "마지막 판과 감지 스냅샷 같음 → no_new_data, 횟수 0"),
    ("failure_keeps_previous", "gate", "만들다 실패 → failed, 최신 판 그대로, 횟수 0"),
    ("auto_not_counted", "gate", "자동 판 1개 + 다시 추천 1번 → 횟수 1"),
    ("lock_expired", "gate", "11분 전에 걸린 잠금(만든 프로세스 죽음) → 다시 추천 시작됨"),
    ("double_click", "single", "같은 프로세스에서 두 번 연달아 → 두 번째 in_progress, 판 1개"),
    ("two_processes", "single", "서버·수집기 두 프로세스가 동시에 → 판 1개"),
    ("versions_kept", "version", "자동 1 + 다시 추천 2 → 판 3개 모두 남음, 기본은 최신"),
    ("rules_split", "version", "같은 날 규칙 버전이 바뀌면 판 목록이 따로"),
    ("same_insight_id", "version", "같은 날 같은 주제는 판이 바뀌어도 같은 insight_id"),
    ("briefs_table_latest", "version", "briefs 표에 최신 판 → 대화 read_insight가 최신 판 주제"),
    ("diff_counts", "version", "[A,B,C] → [A,C,D,E]: 새 2(D,E), 빠짐 1(B)"),
]

EXPECTED = {
    "daily_limit": {"blocked": "daily_limit", "used": 3},
    "cooldown_after_auto": {"blocked": "cooldown", "text_has_time": True},
    "llm_70": {"blocked": "llm_budget"},
    "no_new_data": {"blocked": "no_new_data", "used": 0},
    "failure_keeps_previous": {"state": "failed", "latest_topic": "책추천", "used": 0},
    "auto_not_counted": {"used": 1},
    "lock_expired": {"started": True},
    "double_click": {"second": "in_progress", "versions": 1},
    "two_processes": {"versions": 1, "started": 1},
    "versions_kept": {"versions": 3, "latest_version": 3},
    "rules_split": {"r1": 1, "r2": 1},
    "same_insight_id": {"same": True},
    "briefs_table_latest": {"topic": "두번째판주제"},
    "diff_counts": {"new": ["D", "E"], "gone": ["B"]},
}


def cases() -> list[dict]:
    return [{"name": n, "metric": m, "why": w, "expected": EXPECTED[n]} for n, m, w in CASES]


if __name__ == "__main__":
    json.dump(cases(), sys.stdout, ensure_ascii=False, indent=2)
    print()
