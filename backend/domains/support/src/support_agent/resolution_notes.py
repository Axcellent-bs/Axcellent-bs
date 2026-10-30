"""해결된 문의의 해결 메모(원인·조치·결과): 글을 다듬고 받을 수 있는지 코드가 정한다.

규칙
- 메모는 해결된 문의에만, 상담원이 남긴다. 세 칸(`root_cause`·`action_taken`·`result`) 중
  하나는 있어야 한다.
- 칸마다 앞뒤 공백을 자르고 1000자까지다. 길면 자르지 않고 돌려보낸다
  (상담원이 쓴 글을 마음대로 줄이지 않는다).
- 개인정보(전화번호·이메일·주민등록번호·카드·계좌)는 가려서 저장한다. 비운 칸은 `None`이다.
- 메모는 AI가 쓰지 않는다. LLM을 부르지 않고 코드 규칙만 쓴다.
"""

from __future__ import annotations

from typing import Any

from support_agent import guard

MAX_LENGTH = 1000
FIELDS = ("root_cause", "action_taken", "result")
FIELD_KO = {"root_cause": "실제 원인", "action_taken": "한 조치", "result": "결과"}


def clean(raw: Any) -> str | None:
    """한 칸의 글. 문자열이 아니거나 공백뿐이면 `None`."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def problem(values: dict[str, str | None]) -> str | None:
    """받을 수 없는 이유(없으면 `None`). 순서: 길이 → 전부 비었는지."""
    for key in FIELDS:
        value = values[key]
        if value is not None and len(value) > MAX_LENGTH:
            return f"{FIELD_KO[key]}은(는) {MAX_LENGTH}자까지 적을 수 있다"
    if all(values[key] is None for key in FIELDS):
        return "원인·조치·결과 중 하나는 적어야 한다"
    return None


def mask(values: dict[str, str | None]) -> tuple[dict[str, str | None], list[str]]:
    """저장하기 전에 개인정보를 가린다. 가린 종류도 함께 돌려준다."""
    out: dict[str, str | None] = {}
    kinds: list[str] = []
    for key in FIELDS:
        value = values[key]
        if value is None:
            out[key] = None
            continue
        masked, found = guard.mask_pii(value)
        out[key] = masked
        kinds.extend(k for k in found if k not in kinds)
    return out, kinds
