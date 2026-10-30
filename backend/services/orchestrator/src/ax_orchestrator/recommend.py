"""과거 근거 기반 추천: 비슷한 과거 이벤트에 남은 원인과 조치를 이번 이벤트에 참고로 붙인다.

인사 `find_similar_cases` 결과(`inc.outputs["similar_cases"]`)만 쓰는 읽기 전용 계산이다.
해결 내용(원인·조치)이 있는 과거 이벤트 중 유사도가 가장 높은 것 하나를 고른다.
없으면 근거 없음으로 둔다.
추천은 화면에 보여 줄 뿐 어떤 단계도 바꾸거나 실행하지 않는다(안전 관문과 사람 판단은 그대로).
"""

from __future__ import annotations

from typing import Any

USABLE = ("resolved", "workaround")  # 미해결 이벤트의 기록은 추천 근거로 쓰지 않는다
CAVEAT = "과거 이벤트의 기록이며 이번 이벤트의 원인으로 확정된 것이 아니다. 사람이 확인한다."


def recommend(similar: dict[str, Any] | None) -> dict[str, Any] | None:
    """조회가 아직 없으면 None, 있으면 {status: found | no_evidence, ...}."""
    if similar is None:
        return None
    cases = similar.get("cases") or []
    # 유형이 다른 이벤트의 원인·조치는 추천하지 않는다(화면에는 "다른 유형"으로 보인다)
    same = [c for c in cases if c.get("same_type") is not False]
    usable = [
        c
        for c in same
        if (c.get("resolution") or {}).get("outcome") in USABLE
        and (c["resolution"].get("root_cause") or c["resolution"].get("action"))
    ]
    if not usable:
        if not cases:
            reason = "비슷한 과거 이벤트가 없음"
        elif not same:
            reason = f"같은 유형의 과거 이벤트가 없음 (다른 유형 {len(cases)}건은 참고용)"
        else:
            reason = "비슷한 과거 이벤트는 있으나 해결된 기록(원인·조치)이 없음"
        return {"status": "no_evidence", "reason": reason}
    best = max(usable, key=lambda c: c.get("similarity") or 0.0)
    resolution = best["resolution"]
    return {
        "status": "found",
        "card_id": best.get("card_id"),
        "title": best.get("title"),
        "similarity": best.get("similarity"),
        "outcome": resolution.get("outcome"),
        "root_cause": resolution.get("root_cause"),
        "action": resolution.get("action"),
        "minutes_to_resolve": resolution.get("minutes_to_resolve"),
        "caveat": CAVEAT,
    }
