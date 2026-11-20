"""대화에 넣는 사실. 기획 상태를 읽기만 한다 (받은 요청·기획서·결정·제안).

숫자·상태는 여기서 코드가 모으고, LLM은 이것을 풀어 쓸 뿐이다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

RECENT = 12
TEXT_CHARS = 120
# 제안을 보낸 부서 (사람이 "데이터분석에서 넘어온 제안"처럼 부르면 찾을 수 있게)
FROM_LABEL = {"data": "데이터분석", "marketing": "마케팅", "finance": "재무"}


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


def _when(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _created(p: Any) -> str:
    return _when(getattr(p, "created_at", None)) or ""


def collect(state: Any) -> dict[str, Any]:
    reqs = list(state.requests)
    specs = list(state.specs)
    props = list(getattr(state, "proposals", []))
    decisions = list(state.decisions)
    return {
        "requests": {
            "total": len(reqs),
            "by_state": dict(Counter(r.state for r in reqs)),
            "recent": [
                {
                    "id": r.id,
                    "title": _short(r.title),
                    "state": r.state,
                    "from": r.from_,
                    "spec": r.spec,
                    "duplicate_of": r.duplicate_of,
                }
                for r in reqs[-RECENT:]
            ],  # fmt: skip
        },
        "specs": {
            "total": len(specs),
            "by_status": dict(Counter(s.status for s in specs)),
            "waiting_question": [s.id for s in specs if s.question][:RECENT],
            "recent": [
                {
                    "id": s.id,
                    "title": _short(s.title),
                    "status": s.status,
                    "request": s.request,
                    "auto": s.auto,
                }
                for s in specs[-RECENT:]
            ],  # fmt: skip
        },
        "proposals": {
            "total": len(props),
            "by_status": dict(Counter(p.status for p in props)),
            "recent": [
                {
                    "id": p.id,
                    "title": _short(p.title),
                    "status": p.status,
                    "spec": p.spec,
                    "products": p.products[:5],
                    "result": p.result.direction if p.result else None,
                    "from": getattr(p, "from_", None),
                    "from_label": FROM_LABEL.get(getattr(p, "from_", None) or "", None),
                    "created_at": _when(getattr(p, "created_at", None)),
                    "angle": getattr(p, "angle", None),
                }
                for p in sorted(props, key=_created)[-RECENT:]
            ],  # fmt: skip
        },
        "decisions": [
            {"id": d.id, "spec": d.spec, "text": _short(d.text), "reason": _short(d.reason)}
            for d in decisions[-RECENT:]
        ],
    }
