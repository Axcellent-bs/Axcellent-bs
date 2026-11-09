"""campaign_result 채점기: 케이스 정답(`make_cases`)과 skill 답(③ 매출 효과·⑤ 알림·영상)을 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 skill PR에서 켠다):
- `campaign_result.sales_verdict_accuracy`: 판정(more·less·no_effect·없음)·라벨·low_sample이
  모두 맞는 비율
- `campaign_result.negative_preserved_rate`: 네 칸 숫자가 입력 그대로(마이너스 포함)인 비율.
  네 칸 합산 금지 케이스는 더한 값이 응답의 숫자·문장 어디에도 없어야 맞음
- `campaign_result.alert_accuracy`: 알림 종류(loss·data_gap) 묶음이 같은 비율
- `campaign_result.unmeasurable_label_rate`: 프로모션 링크·쿠폰 없는 영상이 "측정 불가: …"로
  표시된 비율 + "영상 N개 중 매출 측정 가능 M개" 줄이 맞는 비율
답이 없거나 모양이 틀린 케이스는 모두 틀린 것으로 센다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

METRICS = (
    "campaign_result.sales_verdict_accuracy",
    "campaign_result.negative_preserved_rate",
    "campaign_result.alert_accuracy",
    "campaign_result.unmeasurable_label_rate",
)
UNMEASURABLE = "측정 불가: 프로모션 링크/쿠폰 없음"


def _numbers(node: Any) -> list[float]:
    if isinstance(node, bool):
        return []
    if isinstance(node, int | float):
        return [float(node)]
    if isinstance(node, Mapping):
        return [x for v in node.values() for x in _numbers(v)]
    if isinstance(node, list):
        return [x for v in node for x in _numbers(v)]
    return []


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    """케이스 이름 → 답 {"sales": CampaignSalesEffect, "alerts": [...], "videos": [...]}."""
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        sales = got.get("sales") or {}
        verdict = sales.get("verdict") or {}
        count(METRICS[0], (verdict.get("verdict"), verdict.get("label"), verdict.get("low_sample"))
              == (want["verdict"], want["label"], want["low_sample"]))  # fmt: skip
        cells = {c.get("key"): c for c in sales.get("cells") or [] if isinstance(c, Mapping)}
        for key, fields in want["cells"].items():
            for field, value in fields.items():
                count(METRICS[1], (cells.get(key) or {}).get(field, "missing") == value)
        if want["summed"] is not None:
            text = json.dumps(sales, ensure_ascii=False)
            ok = bool(sales) and float(want["summed"]) not in _numbers(sales)
            ok = ok and f"{want['summed']:,}" not in text
            count(METRICS[1], ok)
        kinds = sorted({a.get("kind") for a in got.get("alerts") or [] if isinstance(a, Mapping)})
        count(METRICS[2], bool(got) and kinds == want["alerts"])
        videos = {v.get("video_id"): v for v in got.get("videos") or [] if isinstance(v, Mapping)}
        for vid in want["unmeasurable"]:
            count(METRICS[3], (videos.get(vid) or {}).get("sales_reason") == UNMEASURABLE)
        count(METRICS[3], sales.get("measurable_line") == want["measurable_line"])
    return {m: round(ok / total, 4) if total else 0.0 for m, (ok, total) in hits.items()}
