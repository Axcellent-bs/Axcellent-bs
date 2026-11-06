"""YouTube 할당량 정리 평가 케이스 생성기: 합성 호출 순서 → 정답.

    cd backend
    uv run python domains/data/eval/youtube_quota/make_cases.py

규칙 (이 파일이 직접 계산, 코드와 따로 적음):
- 하루 전체 상한 8,000단위. 새 숏츠 추적(부른 곳 fresh_shorts) 몫 1,200단위를 먼저 떼어 둔다.
  다른 기능은 합쳐서 6,800단위 안에서만 검색(100단위)할 수 있다
- 다른 기능이 몫을 다 써도 새 숏츠 추적은 자기 몫 1,200단위까지 검색할 수 있다 (그 이상은 못 함)
- 지금 인기 보충 검색: 같은 YouTube 카테고리는 하루 최대 2번 (실패한 검색도 1번으로 센다).
  성공한 결과는 6시간 다시 쓴다 (6시간 안에는 다시 검색하지 않음)
"""

from __future__ import annotations

import json
import sys

CAP = 8000
RESERVE = 1200
SEARCH = 100
PER_CATEGORY = 2
CACHE_HOURS = 6


def reserve_case() -> dict:
    """다른 기능이 검색을 계속 시도한 뒤 새 숏츠 추적이 검색을 시도한다."""
    others = (CAP - RESERVE) // SEARCH  # 다른 기능이 할 수 있는 검색 수
    fresh = RESERVE // SEARCH  # 새 숏츠 추적이 할 수 있는 검색 수
    return {
        "name": "reserve",
        "kind": "reserve",
        "cap": CAP,
        "reserve": RESERVE,
        "steps": [
            {"caller": "trend_popular", "tries": 45},
            {"caller": "video_explorer", "tries": 45},
            {"caller": "fresh_shorts", "tries": 20},
        ],
        "expected": {"others_searches": others, "others_units_max": CAP - RESERVE,
                     "fresh_searches": fresh},
    }  # fmt: skip


def fill_case(name: str, outcome: str, hours: list[float]) -> dict:
    """같은 카테고리·검색어 보충을 정해진 시각(그날 0시 기준 시간)마다 부른다."""
    if outcome == "fail":
        calls = min(PER_CATEGORY, len(hours))
    else:
        calls, last = 0, None
        for h in hours:
            if calls < PER_CATEGORY and (last is None or h - last >= CACHE_HOURS):
                calls, last = calls + 1, h
    return {"name": name, "kind": "fill", "outcome": outcome, "category": "23",
            "term": "스텔라이브", "hours": hours,
            "expected": {"max_calls": calls, "cache_hours": CACHE_HOURS}}  # fmt: skip


def cases() -> list[dict]:
    hourly_twice = [h + d for h in range(24) for d in (0.0, 0.01)]  # 수집기 1시간마다 2번 미리 계산
    return [
        reserve_case(),
        fill_case("fill_rate_limited_all_day", "fail", hourly_twice),
        fill_case("fill_ok_all_day", "ok", hourly_twice),
        fill_case("fill_ok_cache_window", "ok", [0, 1, 3, 5.9]),
    ]


def main(argv: list[str]) -> int:
    sys.stdout.write(json.dumps([c["expected"] for c in cases()], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
