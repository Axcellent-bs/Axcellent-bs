"""YouTube 장부 하루 경계·전체 상한 평가 케이스 생성기: 시각·호출 순서 → 정답.

    cd backend
    uv run python domains/data/eval/youtube_day/make_cases.py

규칙 (이 파일에 손으로 적은 정답, 코드와 따로):
- YouTube 장부의 하루 = 미국 태평양 시간(America/Los_Angeles) 0시부터. 서머타임(PDT, UTC−7)이면
  한국 16:00, 표준시(PST, UTC−8)면 한국 17:00에 날이 바뀐다
  (2026-11-01 서머타임 끝, 2027-03-14 서머타임 시작)
- 다음 초기화는 한국 시각으로 "다음 초기화: 오늘 17:00 KST" / "내일 16:00 KST"처럼 적는다
  (오늘·내일은 한국 날짜 기준)
- 전체 상한 8,000은 어떤 호출(검색 100, 1단위 호출)도 넘지 않는다. 예약 도입 전에 다른 기능이 많이
  쓴 날은 새 숏츠 추적도 남은 만큼만 쓴다
"""

from __future__ import annotations

import json
import sys

CAP = 8000
RESERVE = 1200


def _day(name: str, at: str, day: str, label: str, dst: bool = False) -> dict:
    return {"name": name, "kind": "day", "at": at, "dst": dst,
            "expected": {"day": day, "label": f"다음 초기화: {label}"}}  # fmt: skip


def day_cases() -> list[dict]:
    return [
        # 2026-11-06 (표준시): 한국 17:00 경계
        _day("pst_before", "2026-11-06T07:59:00Z", "2026-11-05", "오늘 17:00 KST"),
        _day("pst_after", "2026-11-06T08:01:00Z", "2026-11-06", "내일 17:00 KST"),
        # 2026-11-01 서머타임 끝: 그날 0시는 PDT(한국 16:00), 다음 0시는 PST(한국 다음날 17:00)
        _day("fall_before", "2026-11-01T06:59:00Z", "2026-10-31", "오늘 16:00 KST", dst=True),
        _day("fall_after", "2026-11-01T07:01:00Z", "2026-11-01", "내일 17:00 KST", dst=True),
        # 2027-03-14 서머타임 시작: 그날 0시는 PST(한국 17:00), 다음 0시는 PDT(한국 다음날 16:00)
        _day("spring_before", "2027-03-14T07:59:00Z", "2027-03-13", "오늘 17:00 KST", dst=True),
        _day("spring_after", "2027-03-14T08:01:00Z", "2027-03-14", "내일 16:00 KST", dst=True),
    ]  # fmt: skip


def cap_case(name: str, used_before: int, fresh_tries: int, unit_calls: int) -> dict:
    """다른 기능이 이미 `used_before`단위를 쓴 날: 새 숏츠 검색 시도 → 수집기 1단위 호출."""
    room = CAP - used_before
    fresh = min(fresh_tries, RESERVE // 100, room // 100)
    units = min(unit_calls, room - fresh * 100)
    return {"name": name, "kind": "cap", "cap": CAP, "reserve": RESERVE,
            "used_before": used_before, "fresh_tries": fresh_tries, "unit_calls": unit_calls,
            "expected": {"fresh_searches": fresh, "unit_calls": units, "over_cap": 0}}  # fmt: skip


def cases() -> list[dict]:
    return [
        *day_cases(),
        cap_case("today_7950", 7950, 5, 100),  # 오늘처럼 예약 전에 많이 쓴 날
        cap_case("used_7500", 7500, 8, 100),  # 새 숏츠는 남은 500만큼(검색 5번)
        cap_case("quiet_day", 2000, 8, 100),  # 여유 있는 날: 그대로
    ]


def main(argv: list[str]) -> int:
    sys.stdout.write(json.dumps([c["expected"] for c in cases()], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
