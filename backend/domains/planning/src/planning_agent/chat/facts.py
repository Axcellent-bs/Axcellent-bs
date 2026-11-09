"""대화에 넣는 사실. 기획 상태를 읽기만 한다 (받은 요청·기획서·결정·제안).

숫자·상태는 여기서 코드가 모으고, LLM은 이것을 풀어 쓸 뿐이다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

RECENT = 12
TEXT_CHARS = 120


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


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
                }
                for p in props[-RECENT:]
            ],  # fmt: skip
        },
        "decisions": [
            {"id": d.id, "spec": d.spec, "text": _short(d.text), "reason": _short(d.reason)}
            for d in decisions[-RECENT:]
        ],
    }
