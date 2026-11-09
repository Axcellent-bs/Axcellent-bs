"""새 숏츠 추적 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `fresh_shorts.window_match_rate`: 영상마다 fresh_track 스냅샷 수(60초 초과 제외·8일 뒤 종료가
  맞아야 같음)와 형식·시점마다 기준 표본 수·중앙값(인기 목록 스냅샷은 세지 않음)이 정답과 같은 비율
- `fresh_shorts.cap_compliance`: 하루 상한 케이스의 찾기 수·건너뛴 찾기 수·추적 횟수·쓴 단위,
  본 케이스의 하루 최대 사용 단위가 정답과 같은 비율 (상한 2,500을 넘으면 틀림)
- `fresh_shorts.label_accuracy`: 기준 표본 5개 미만일 때 신호 문구가 "기준 모으는 중 (n/5)"인 비율
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "fresh_shorts.window_match_rate",
    "fresh_shorts.cap_compliance",
    "fresh_shorts.label_accuracy",
)


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        got = answers.get(case["name"]) or {}
        if case["name"] == "window":
            want = case["expected"]
            snaps = got.get("snap_counts") or {}
            for vid, n in want["snap_counts"].items():
                count(METRICS[0], snaps.get(vid) == n)
            count(METRICS[0], set(snaps) == set(want["snap_counts"]))  # 60초 초과는 없어야 함
            base = got.get("baseline") or {}
            for fmt, cps in want["baseline"].items():
                for cp, w in cps.items():
                    g = (base.get(fmt) or {}).get(cp) or {}
                    count(METRICS[0], (g.get("n"), g.get("median")) == (w["n"], w["median"]))
            used = got.get("used")
            count(METRICS[1], used == want["used"] and used is not None and used <= case["cap"])
        elif case["name"] == "cap":
            for key, value in case["expected"].items():
                count(METRICS[1], got.get(key) == value)
        elif case["name"] == "label":
            labels = got.get("labels") or {}
            for item in case["labels"]:
                count(METRICS[2], labels.get(str(item["n"])) == item["want"])
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}
