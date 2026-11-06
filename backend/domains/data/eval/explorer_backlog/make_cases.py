"""감지 탭 숏폼 칸: 영상 탐색 밀린 길이 확인·분류 대기 평가 케이스 생성기 (합성 입력 → 정답).

    cd backend
    uv run python domains/data/eval/explorer_backlog/make_cases.py

규칙 (이 파일에 손으로 적은 정답, 코드와 따로):
- 다시 읽기(videos.list, 50개 = 1유닛) 하루 상한 50유닛. 하루는 YouTube 장부 날짜(태평양 0시).
  한국 날짜가 바뀌어도 장부 날짜가 같으면 더 읽지 않는다
- 다시 읽기 순서: 숏 후보(숏 검색 결과, 또는 길이를 아는 4분 미만 영상)가 먼저, 그 안에서 최근
  것(게시 시각을 알면 게시 시각, 모르면 검색한 날)부터
- 분류 묶음: 한 번에 여러 영상을 물어도 결과는 각 영상 자기 것 (답 순서가 섞이거나 빠져도)
- 분류 순서: 숏츠(60초 이하)가 먼저. LLM 하루 상한의 60% 이상(브리프 몫 80% 전)이면 숏츠만 묻고
  롱은 다음 날
"""

from __future__ import annotations

import json
import sys

UNITS = 50
PAGE = 50
SHORT_HINT_SECONDS = 240

# 다시 읽기 상한: 한국 23시 → 한국 다음날 1시(같은 장부 날짜) → 장부 날짜가 바뀐 뒤
CAP_RUNS = ["2026-11-06T14:00:00Z", "2026-11-06T16:00:00Z", "2026-11-07T09:00:00Z"]


def cap_case() -> dict:
    return {"name": "reread_cap", "kind": "reread_cap", "runs": CAP_RUNS, "longs": 4000,
            "expected": {"calls": [UNITS, 0, UNITS]}}  # fmt: skip


def order_case() -> dict:
    """1유닛(50개)만 읽을 수 있을 때 무엇을 먼저 읽나."""
    now = "2026-11-06T03:00:00Z"
    sample = [{"video_id": f"ss{d}{k}", "day_ago": d} for d in range(1, 7) for k in range(5)]
    fill_short = [{"video_id": f"fs{k:02d}", "seconds": 30, "hours_ago": 2 + k} for k in range(40)]
    fill_long = [{"video_id": f"fl{k:03d}", "seconds": 600, "hours_ago": 1} for k in range(500)]
    # 숏 후보 = 숏 검색 표본 30 + 길이 30초 보충 40. 최근 순: 보충(오늘 게시) 40 → 표본 1일 전 5
    # → 2일 전 5 → … 50개면 보충 40 + 표본 1·2일 전 10
    want = [f["video_id"] for f in fill_short] + [s["video_id"] for s in sample
                                                  if s["day_ago"] <= 2]  # fmt: skip
    return {"name": "reread_order", "kind": "reread_order", "now": now, "units": 1,
            "shorts_sample": sample, "fill": fill_long + fill_short,
            "expected": {"read": sorted(want)}}  # fmt: skip


def batch_case() -> dict:
    words = {"게임": "게임", "웃긴": "유머", "요리": "생활·취미"}
    videos = []
    for n in range(30):
        word = list(words)[n % 3]
        videos.append({"video_id": f"b{n:02d}", "title": f"{word} 영상 {n}", "want": words[word]})
    drop = "b07"  # LLM 답에서 빠진 영상 → 분류 못 함 (다른 영상 결과를 받지 않음)
    return {"name": "batch_isolation", "kind": "batch", "videos": videos, "drop": drop,
            "expected": {v["video_id"]: ("unknown" if v["video_id"] == drop else v["want"])
                         for v in videos}}  # fmt: skip


def near_cap_case(name: str, share: float, classify_max: int) -> dict:
    shorts = [{"video_id": f"s{n:02d}", "seconds": 30, "views": 100 + n} for n in range(15)]
    longs = [{"video_id": f"l{n:02d}", "seconds": 900, "views": 10_000 + n} for n in range(15)]
    want = [v["video_id"] for v in shorts]  # 둘 다 숏츠 15개만 (가까우면 숏츠만 / 아니면 숏츠 먼저)
    return {"name": name, "kind": "near_cap", "share": share, "classify_max": classify_max,
            "videos": shorts + longs, "expected": {"asked": sorted(want)}}  # fmt: skip


def cases() -> list[dict]:
    return [
        cap_case(),
        order_case(),
        batch_case(),
        near_cap_case("near_cap_shorts_only", 0.65, 200),
        near_cap_case("shorts_first_room_15", 0.10, 15),
    ]


def main(argv: list[str]) -> int:
    sys.stdout.write(json.dumps([c["expected"] for c in cases()], ensure_ascii=False)[:2000] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
