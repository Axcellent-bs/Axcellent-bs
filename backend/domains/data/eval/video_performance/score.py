"""video_performance 채점기: 합성 곡선 케이스(`make_cases`)의 정답과 skill 답(시점 4개)을 비교.

지표 (평가 정의 PR에서는 관찰만 하고 관문은 켜지 않는다):
- `video_performance.status_accuracy`: 시점마다 known·unknown이 맞는 비율. unknown 정답(데이터
  끊김)은 이유에 "놓침"이 있어야 맞음
- `video_performance.value_exact_rate`: known 정답 시점에서 조회·좋아요·댓글·게시 후 시간이 모두
  같은 비율
- `video_performance.ratio_max_error`: known 정답 시점의 시간당 조회·좋아요 비율·댓글 비율·목표
  대비·같은 숏츠 대비 중 가장 큰 절대 오차 (답에 값이 없으면 오차는 무한대)
답이 없거나 모양이 틀린 케이스는 모든 시점을 틀린 것으로 센다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

METRICS = (
    "video_performance.status_accuracy",
    "video_performance.value_exact_rate",
    "video_performance.ratio_max_error",
)
VALUES = ("views", "likes", "comments", "hours_after")
RATIOS = ("views_per_hour", "like_rate", "comment_rate", "target_ratio", "peer_ratio")


def _by_checkpoint(answer: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(answer, Mapping):
        return {}
    rows = answer.get("checkpoints") or []
    return {c.get("checkpoint"): c for c in rows if isinstance(c, Mapping)}


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    """케이스 이름 → skill 답(VideoPerformanceOutput JSON). 지표 세 개."""
    status_ok = status_all = exact_ok = exact_all = 0
    worst = 0.0
    for case in cases:
        got = _by_checkpoint(answers.get(case["name"]))
        for name, want in case["expected"].items():
            mine = got.get(name) or {}
            status_all += 1
            if want["status"] == "unknown":
                status_ok += mine.get("status") == "unknown" and want["reason_word"] in (
                    mine.get("reason") or "")  # fmt: skip
                continue
            status_ok += mine.get("status") == "known"
            exact_all += 1
            exact_ok += all(mine.get(k) == want[k] for k in VALUES)
            for k in RATIOS:
                a, b = mine.get(k), want[k]
                if b is None:
                    continue
                worst = max(worst, math.inf if a is None else abs(float(a) - float(b)))
    return {
        METRICS[0]: round(status_ok / status_all, 4) if status_all else 0.0,
        METRICS[1]: round(exact_ok / exact_all, 4) if exact_all else 0.0,
        METRICS[2]: round(worst, 6),
    }
