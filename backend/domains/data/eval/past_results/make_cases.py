"""인사이트 "지난번 결과"(past_results) 평가 케이스 생성기: 합성 캠페인 결과 + 브리프 → 정답.

    cd backend
    uv run python domains/data/eval/past_results/make_cases.py

규칙 (이 파일이 직접 계산, skill과 따로 적음):
- 연결: 인사이트의 insight_id(또는 예전 id legacy_insight_id·legacy_insight_ids)와 캠페인 결과의
  insight_id가 같으면 연결. 주제 이름은 보지 않는다. 시험 캠페인은 뺀다
- 최근 3개까지, 측정 기간 시작(window_from)이 늦은 것부터
- 띠 색 (가장 최근 1개): 매출 덜 팔림이거나 알림 loss → red, 조회 달성 + 매출 더 팔림 → green,
  그 밖 → gray. 결과가 없으면 "none" (화면은 회색 "지난번 결과 없음")
- 조회 판정: 7일이 끝난 영상(달성·미달)의 목표 대비 중앙값 ≥ 1 → 달성, 미만 → 미달,
  끝난 영상이 없고 측정 중이 있으면 측정 중 (예상치는 넣지 않음)
- 조회 달성 + 매출 덜 팔림이면 셋째 줄은 반대 문구
- 지난번 결과를 붙여도 인사이트 순서·판정 칸은 그대로
"""

from __future__ import annotations

import json
import sys

CONTRA = "조회와 매출이 반대로 나왔습니다. 다시 한다면 쿠폰 조건을 기획·재무와 먼저 확인하세요."
LABELS = {"more": "더 팔림", "less": "덜 팔림", "no_effect": "효과 확인 안 됨"}


def _video(vid: str, verdict: str, ratio: float | None, test: bool = False) -> dict:
    waiting = "측정 중 (40.0시간 뒤 잼)"
    cps = [{"checkpoint": "7d", "status": "known" if ratio is not None else "unknown",
            "views": round((ratio or 0) * 5000) if ratio is not None else None,
            "target_ratio": ratio, "reason": None if ratio is not None else waiting}]  # fmt: skip
    return {"video_id": vid, "title": vid, "test": test, "verdict": verdict, "checkpoints": cps,
            "actions": [f"{vid} 개선 방향"]}  # fmt: skip


def _result(cid: str, insight_id: str, start: str, videos: list[dict], sales: str | None,
            inc: int | None = None, orders: int | None = None, loss: bool = False,
            test: bool = False) -> dict:  # fmt: skip
    verdict = None if sales is None else {
        "status": "known", "verdict": sales, "label": LABELS[sales], "low_sample": False,
        "incremental_gross_krw": inc, "incremental_orders": orders}  # fmt: skip
    return {
        "status": "known", "campaign_id": cid, "campaign_name": cid, "insight_id": insight_id,
        "window_from": start, "window_to": start.replace("T00", "T23"), "test": test,
        "videos": [{k: v for k, v in x.items() if k != "actions"} for x in videos],
        "sales": {"status": "known" if sales else "unknown", "verdict": verdict, "cells": [],
                  "measurable_line": "영상 1개 중 매출 측정 가능 1개"},
        "advice": [{"video_id": x["video_id"], "name": x["video_id"], "verdict": x["verdict"],
                    "actions": x["actions"]} for x in videos if not x["test"]],
        "alerts": [{"kind": "loss", "message": f"{cid} 캠페인 매출이 평소보다 줄었습니다"}]
        if loss else [],
    }  # fmt: skip


def brief() -> dict:
    """브리프 인사이트 5개 (순서·판정 칸은 붙인 뒤에도 그대로여야 함)."""
    rows = [
        ("ins_none", "캠페인 없는 주제", {}),
        ("ins_four", "캠페인 네 번", {}),
        ("ins_contra", "조회 달성 매출 감소", {}),
        ("ins_measure", "측정 중", {}),
        ("ins_new_id", "책 추천 (이름 바뀜)", {"legacy_insight_ids": ["ins_old_id"]}),
    ]
    return {"status": "known", "insights": [
        {"insight_id": i, "rank": n + 1, "topic": t, "line": f"{t} 한 줄", "category": "교육·책",
         "ad_material": True, **extra} for n, (i, t, extra) in enumerate(rows)]}  # fmt: skip


def results() -> list[dict]:
    good = [_video("vgood", "achieved", 2.0)]
    return [
        _result("c_four_1", "ins_four", "2026-10-01T00:00:00Z", good, "more", 300_000, 10),
        _result("c_four_2", "ins_four", "2026-10-10T00:00:00Z", good, "no_effect", 10_000, 1),
        _result("c_four_3", "ins_four", "2026-10-20T00:00:00Z", good, "more", 500_000, 20),
        _result("c_four_4", "ins_four", "2026-10-30T00:00:00Z", good, "more", 900_000, 30),
        _result("c_contra", "ins_contra", "2026-11-01T00:00:00Z", [_video("vc", "achieved", 2.5)],
                "less", -700_000, -15, loss=True),
        _result("c_measure", "ins_measure", "2026-11-04T00:00:00Z",
                [_video("vm", "measuring", None)], "no_effect", 50_000, 2),
        _result("c_old_id", "ins_old_id", "2026-11-02T00:00:00Z", [_video("vo", "missed", 0.6)],
                "no_effect", -20_000, -1),
        _result("c_test", "ins_none", "2026-11-03T00:00:00Z", [_video("vt", "achieved", 3.0, True)],
                "more", 1, 1, test=True),
    ]  # fmt: skip


def expected() -> dict:
    return {
        "links": {"ins_none": [], "ins_four": ["c_four_4", "c_four_3", "c_four_2"],
                  "ins_contra": ["c_contra"], "ins_measure": ["c_measure"],
                  "ins_new_id": ["c_old_id"]},
        "bands": {"ins_none": "none", "ins_four": "green", "ins_contra": "red",
                  "ins_measure": "gray", "ins_new_id": "gray"},
        "notes": {"ins_contra": CONTRA},
        "views": {"ins_four": "achieved", "ins_contra": "achieved", "ins_measure": "measuring",
                  "ins_new_id": "missed"},
        "no_estimate": ["ins_measure"],
    }  # fmt: skip


def cases() -> list[dict]:
    return [{"name": "past", "brief": brief(), "results": results(), "expected": expected()}]


def main(argv: list[str]) -> int:
    sys.stdout.write(json.dumps(cases()[0]["expected"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
