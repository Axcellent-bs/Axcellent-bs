"""인사이트 브리프 "기획에 넘기는 기준"·홍보 영상(promo) 평가 케이스 생성기.

    cd backend
    uv run python domains/data/eval/insight_gate/make_cases.py

규칙 (이 파일이 직접 적음, 코드와 따로):
- 넘김 = (같은 형식 숏츠 시간당 조회 ÷ 전체 숏츠 중앙값 ≥ 1.5 또는 네이버 검색 배수 ≥ 1.3)
  그리고 최근 3일 새로 잡힌 같은 형식 숏츠 ≥ 5개
- 근거로 치지 않음: 가격만, 배수 1.0 미만(줄어듦), 표본 부족, 캣북스가 팔지 않는 상품군
- 못 넘은 것은 기획에 넘기지 않고 이유를 남긴다 (트렌드 인사이트는 참고용, 형식은 held_back)
- 홍보 영상 아이디어는 LLM 없이도 고정 틀 "{형식}으로/로 캣북스의 … 보여 주기" + fallback 표시
- 숫자는 입력 값 그대로 (정답의 이유 문장도 입력 숫자만 쓴다)
"""

from __future__ import annotations

import json
import sys

PRICE_ONLY = "트렌드 근거 없음(가격만)"
NOT_SOLD = "캣북스가 팔지 않는 상품군"

# 숏츠 형식 통계 (전체 숏츠 시간당 조회 중앙값 100 기준)
BASE_VPH = 100.0


def cases() -> list[dict]:
    """(이름, 종류, 입력, 정답).

    종류 trend = 트렌드 인사이트 한 개의 기준, promo = 숏츠 형식 하나의 홍보 영상.
    """
    out = [
        {
            "name": "price_only_product",
            "kind": "trend",
            "input": {"topic": "그림책 세트", "fmt": None, "naver": None, "season": None,
                      "price_only": True, "not_sold": False},
            "expected": {"passed": False, "reason": PRICE_ONLY},
        },
        {
            "name": "search_falling_0_8",
            "kind": "trend",
            "input": {"topic": "겨울 다이어리", "fmt": None, "naver": 0.8, "season": None,
                      "price_only": False, "not_sold": False},
            "expected": {"passed": False, "reason": "네이버 검색이 평소의 0.8배(줄어듦)"},
        },
        {
            "name": "stationery_not_sold",
            "kind": "trend",
            "input": {"topic": "만년필 입문", "fmt": "정보·팁", "naver": None, "season": 1.78,
                      "price_only": False, "not_sold": True},
            "expected": {"passed": False, "reason": NOT_SOLD},
        },
        {
            "name": "format_rising_6_new",
            "kind": "promo",
            "input": {"format": "브이로그", "vph": 160.0, "recent": 6},
            "expected": {"passed": True, "views_ratio": 1.6, "recent": 6, "fallback": True,
                         "idea": "브이로그로 캣북스의 하루 독서 시간 보여 주기"},
        },
        {
            "name": "format_rising_but_2_new",
            "kind": "promo",
            "input": {"format": "리액션", "vph": 160.0, "recent": 2},
            "expected": {"passed": False,
                         "reason": "최근 3일 같은 형식 숏츠 2개 (기준 5개, 반짝)"},
        },
    ]  # fmt: skip
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(cases(), ensure_ascii=False, indent=1))
