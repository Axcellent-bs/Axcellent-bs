"""보고서 추천 형식 카드를 제작안에 (11/26).

데이터 트렌드 보고서(#1852)가 웃음 재료(상황 요약)와 추천 형식 카드 id를 만들어 기획 입력
(`format_ids`·`humor_situations`)으로 보낸다. 기획은 카드를 `ax_agent_template.format_cards`(공용)로
읽어 첫 카드의 이름·비트(구간 비율)·훅·펀치라인 위치를 "형식 예시"로 지시문에 넣는다(우리가 쓴
카드라 지시문에 넣어도 된다). 상황은 고객·외부 글에서 나온 요약이라 데이터 칸(untrusted)에만.

- 카드 고르기: 추천 순서대로, recommend=false·한 화면 인물 2명 초과 카드는 건너뛴다
- 먼저 지키는 것: 의뢰 지시(request_rules)·제작 검사·회사 캐릭터가 형식보다 먼저다. LLM이 형식을
  쓰지 않았거나(장면 beat_ko가 비었음) 의뢰 지시를 못 지키면 형식 없이(format_id "")
- 장면 시간: 카드 비트 비율로 나누되, 첫 훅(0~2초)·마지막 장면 길이·장면 수 = 구간 수 같은 지금
  규칙과 부딪히면 지금 규칙이 이긴다
"""

from __future__ import annotations

from typing import Any

from ax_agent_template import format_cards

MIN_SCENES, MAX_SCENES = 3, 6  # video_plan.MIN_SCENES·MAX_SCENES와 같다
SITUATION_MAX = 80


def card_for(p: Any) -> format_cards.FormatCard | None:
    """제안의 추천 카드 중 쓸 첫 카드. 없으면 None (지금처럼 형식 없이)."""
    ids = list(getattr(p, "format_ids", None) or [])
    if not ids:
        return None
    try:
        known = format_cards.by_id()
    except (OSError, format_cards.FormatCardError):
        return None
    for cid in ids:
        card = known.get(str(cid))
        if card is None or not card.recommend or card.people > format_cards.MAX_PEOPLE:
            continue
        if MIN_SCENES <= len(card.beats) <= MAX_SCENES:
            return card
    return None


def beats_line(card: format_cards.FormatCard) -> str:
    """ "기대 20% → 현실 25% → 펀치 리액션 35%(펀치) → 엔드카드 20%" """
    return " → ".join(
        f"{b.label} {round(b.ratio * 100)}%" + ("(펀치)" if b.punch else "") for b in card.beats
    )


def prompt_block(card: format_cards.FormatCard) -> str:
    """지시문 끝에 붙는 형식 예시 (카드가 있을 때만)."""
    return (
        "- 형식 예시 (보고서 추천 형식 카드). 의뢰 지시·제작 검사·회사 캐릭터가 이 형식보다 "
        "먼저다. 의뢰 지시와 맞지 않으면 형식을 쓰지 말고 장면 beat_ko를 모두 빈 문자열로 둔다:\n"
        f"  - 이름: {card.name} ({card.id})\n"
        f"  - 비트(구간 비율): {beats_line(card)}. 장면은 비트마다 하나씩, "
        f"장면 {len(card.beats)}개\n"
        f"  - 훅: {card.hook}\n"
        f"  - 펀치라인: {card.punchline}에서 터진다\n"
        "  - 장면마다 beat_ko에 그 장면이 맡은 비트 이름을 그대로 적는다. 상황은 데이터 블록 "
        "humor_situations에서 고른다(없으면 주제에서)\n"
    )


def situations(p: Any) -> list[str]:
    """데이터 칸에 넣을 상황 요약 (untrusted)."""
    return [str(s.text_ko)[:SITUATION_MAX] for s in getattr(p, "humor_situations", None) or []
            if str(getattr(s, "text_ko", "")).strip()]  # fmt: skip


def situation_for(p: Any, card: format_cards.FormatCard) -> str:
    """제작안 상황 한 줄: 그 카드로 태그된 상황, 없으면 첫 상황."""
    rows = list(getattr(p, "humor_situations", None) or [])
    tagged = [s for s in rows if getattr(s, "format_id", "") == card.id]
    pick = (tagged or rows or [None])[0]
    return str(getattr(pick, "text_ko", "") or "")[:SITUATION_MAX] if pick else ""


def beat_cuts(
    card: format_cards.FormatCard, duration: int, n: int, hook: int | None, last: int | None
) -> list[tuple[int, int]] | None:
    """카드 비트 비율로 나눈 (시작, 끝). 장면 수가 비트 수와 다르면 None (지금 규칙).

    hook·last: 지금 규칙의 첫 장면 끝(훅 0~2초)·마지막 장면 시작. 있으면 그 자리를 지키고 그 사이를
    가운데 비트 비율로 나눈다."""
    if n != len(card.beats):
        return None
    ratios = [b.ratio for b in card.beats]
    if hook is not None and last is not None and n >= 3:
        mid = ratios[1:-1]
        total = sum(mid)
        cuts = [0, hook]
        at = 0.0
        for r in mid[:-1]:
            at += r / total
            cuts.append(round(hook + (last - hook) * at))
        cuts += [last, duration]
    else:
        cuts = [0]
        at = 0.0
        for r in ratios[:-1]:
            at += r
            cuts.append(round(duration * at))
        cuts.append(duration)
    pairs = [(cuts[i], cuts[i + 1]) for i in range(n)]
    return pairs if all(a < b for a, b in pairs) else None
