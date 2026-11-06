"""인사이트 "지난번 결과" 채점기.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `past_results.link_accuracy`: 인사이트마다 연결된 캠페인 id 목록(최근 3개, 최신순, 시험 캠페인
  제외, 예전 id 연결)과 조회 판정이 정답과 같은 비율
- `past_results.band_accuracy`: 띠 색(가장 최근 1개, 없으면 none), 반대 문구, 측정 중에 예상치가
  없는지가 맞는 비율
- `past_results.invariance_rate`: 붙인 뒤에도 인사이트 순서·판정 칸(past_results 밖 전부)이
  그대로인지
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

METRICS = (
    "past_results.link_accuracy",
    "past_results.band_accuracy",
    "past_results.invariance_rate",
)


def _strip(insight: Mapping[str, Any]) -> dict:
    return {k: v for k, v in insight.items() if k != "past_results"}


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        after = got.get("after") or {}
        before = got.get("before") or {}
        by = {i.get("insight_id"): i for i in after.get("insights") or []}
        for iid, ids in want["links"].items():
            past = (by.get(iid) or {}).get("past_results")
            count(METRICS[0], past is not None and [p.get("campaign_id") for p in past] == ids)
        for iid, view in want["views"].items():
            past = (by.get(iid) or {}).get("past_results") or [{}]
            count(METRICS[0], past[0].get("views_verdict") == view)
        for iid, band in want["bands"].items():
            past = (by.get(iid) or {}).get("past_results")
            got_band = None if past is None else (past[0].get("band") if past else "none")
            count(METRICS[1], got_band == band)
        for iid, note in want["notes"].items():
            past = (by.get(iid) or {}).get("past_results") or [{}]
            count(METRICS[1], past[0].get("note_line") == note)
        for iid in want["no_estimate"]:
            past = (by.get(iid) or {}).get("past_results") or [{}]
            text = json.dumps(past[0], ensure_ascii=False)
            count(METRICS[1], bool(past[0]) and "예상" not in text and "projection" not in text)
        order_before = [i.get("insight_id") for i in before.get("insights") or []]
        order_after = [i.get("insight_id") for i in after.get("insights") or []]
        count(METRICS[2], bool(order_after) and order_after == order_before)
        pairs = zip(after.get("insights") or [], before.get("insights") or [], strict=False)
        same = [_strip(a) == _strip(b) for a, b in pairs]
        count(METRICS[2], bool(same) and all(same))
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}
