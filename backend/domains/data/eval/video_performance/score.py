"""video_performance 채점기: 합성 곡선 케이스(`make_cases`)의 정답과 skill 답을 비교.

지표 (관문은 skill을 고치는 PR에서 켠다):
- `video_performance.status_accuracy`: 시점마다 known·unknown이 맞는 비율. unknown 정답은 이유에
  정답 말("놓침"·"측정 중")이 있어야 맞음
- `video_performance.value_exact_rate`: known 정답 시점에서 조회·좋아요·댓글·게시 후 시간이 모두
  같은 비율
- `video_performance.ratio_max_error`: known 정답 시점의 시간당 조회·좋아요 비율·댓글 비율·목표
  대비·같은 숏츠 대비 중 가장 큰 절대 오차 (답에 값이 없으면 무한대. 정답이 None인데 답에 값이
  있어도 무한대: 목표 대비는 목표 시점(7일)에만 있어야 한다)
- `video_performance.aggregate_accuracy`: 섞인 시점 케이스에서 성과 모으기 시점별 줄
  (`performance_by_horizon`, 인사이트 묶음)이 정답과 같은 비율. n·빠진 수·low_sample·중앙값을
  모두 맞혀야 맞음
- 성과 요인 시나리오 (`score_scenario`, 지표 `SCENARIO_METRICS`): 시간 맞춤 기준, 판정, 신호 등급,
  문장 안 숫자 일치율, 문구(비교·데이터 문제·예상치), 예상치 표기율
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
AGGREGATE_METRIC = "video_performance.aggregate_accuracy"
VALUES = ("views", "likes", "comments", "hours_after")
RATIOS = ("views_per_hour", "like_rate", "comment_rate", "target_ratio", "peer_ratio")
AGG_FIELDS = ("videos", "n", "excluded", "excluded_measuring", "excluded_missed", "low_sample",
              "median_views", "median_peer_ratio", "median_target_ratio")  # fmt: skip


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
                    worst = worst if a is None else math.inf
                    continue
                worst = max(worst, math.inf if a is None else abs(float(a) - float(b)))
    return {
        METRICS[0]: round(status_ok / status_all, 4) if status_all else 0.0,
        METRICS[1]: round(exact_ok / exact_all, 4) if exact_all else 0.0,
        METRICS[2]: round(worst, 6),
    }


SCENARIO_METRICS = (
    "video_performance.age_baseline_accuracy",  # 시간 맞춤 기준 표본 수·중앙값·배수 (①)
    "video_performance.verdict_accuracy",  # 판정 (달성·미달·측정 중)
    "video_performance.signal_level_accuracy",  # 신호 4개 등급 (③ 성장 판단 불가, ⑤ 표본 부족)
    "video_performance.factor_number_rate",  # 문장에 계산값 숫자가 그대로 들어간 비율
    "video_performance.factor_phrase_rate",  # 비교(②)·데이터 문제(③)·예상치(④) 문구
    "video_performance.estimate_label_rate",  # 측정 중 예상치: is_estimate·값·"(예상치)" (④)
)
SIGNALS = ("first", "like", "cmt", "grow")


def score_scenario(
    cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]
) -> dict[str, float]:
    """케이스 이름 → list_all 답(JSON, videos[]). 영상마다 정답과 비교."""
    hits = {m: [0, 0] for m in SCENARIO_METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        answer = answers.get(case["name"])
        rows = answer.get("videos") if isinstance(answer, Mapping) else None
        by_id = {v.get("video_id"): v for v in rows or [] if isinstance(v, Mapping)}
        for want in case["answers"]:
            got = by_id.get(want["id"]) or {}
            cps = _by_checkpoint(got)
            for name, e in want["checkpoints"].items():
                if e["status"] != "known":
                    continue
                c = cps.get(name) or {}
                keys = ("baseline_n", "baseline_views", "peer_age_ratio")
                count(SCENARIO_METRICS[0], all(c.get(k, "missing") == e[k] for k in keys))
            count(SCENARIO_METRICS[1], got.get("verdict") == want["verdict"])
            signals = got.get("signals") or {}
            for s in SIGNALS:
                count(SCENARIO_METRICS[2],
                      (signals.get(s) or {}).get("level") == want["levels"][s])  # fmt: skip
            text = " ".join(got.get("factors") or [])
            for n in want["numbers"]:
                count(SCENARIO_METRICS[3], n in text)
            for p in want["phrases"]:
                count(SCENARIO_METRICS[4], p in text)
            if want["projection"] is not None:
                p = got.get("projection") or {}
                same = all(p.get(k) == want["projection"][k] for k in ("views", "ratio"))
                count(SCENARIO_METRICS[5], p.get("is_estimate") is True and "(예상치)" in text
                      and same)  # fmt: skip
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}


def score_aggregate(
    cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]
) -> dict[str, float]:
    """케이스 이름 → 마지막 skill 답(JSON). 시점마다 인사이트 묶음 줄이 정답과 같은 비율."""
    ok = total = 0
    for case in cases:
        answer = answers.get(case["name"])
        rows = answer.get("performance_by_horizon") if isinstance(answer, Mapping) else None
        mine = {r.get("checkpoint"): r for r in rows or []
                if isinstance(r, Mapping) and r.get("key") == "insight"}  # fmt: skip
        for horizon, want in case["aggregate"].items():
            total += 1
            got = mine.get(horizon) or {}
            ok += all(got.get(k, "missing") == want[k] for k in AGG_FIELDS)
    return {AGGREGATE_METRIC: round(ok / total, 4) if total else 0.0}
