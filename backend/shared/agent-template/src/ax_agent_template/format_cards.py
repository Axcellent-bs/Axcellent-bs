"""짧은 영상 형식 카드 라이브러리 (11/26). 데이터 파일 `format_cards.toml`을 표준 라이브러리로
읽는다.

데이터 부서 보고서가 "추천 형식 카드"를 고를 때 쓴다(카드 id만 보고서에 남긴다). 기획 few-shot
연결은 아직 하지 않는다. 카드 내용은 사람이 검토한다(일반 형식만, 특정 영상·채널·실존 인물·
대사 없음).
형식 검사(`load_cards`): id 모양·겹침, 구간 비율 합 1, 펀치 구간 번호, 펀치라인 위치·난이도 값.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CARDS_PATH = Path(__file__).with_name("format_cards.toml")
PUNCHLINES = ("자막", "표정", "컷")
MAX_PEOPLE = 2  # 한 화면 인물 상한. 이보다 많은 카드는 추천에 쓰지 않는다(recommend = false)
DIFFICULTIES = ("쉬움", "보통", "어려움")
_ID = re.compile(r"^F-\d{2}$")


class FormatCardError(ValueError):
    """카드 파일 형식이 틀렸다."""


@dataclass(frozen=True)
class Beat:
    label: str
    ratio: float
    punch: bool


@dataclass(frozen=True)
class FormatCard:
    id: str
    name: str
    beats: tuple[Beat, ...]
    hook: str
    punchline: str
    people: int
    ai_difficulty: str
    audio_names: tuple[str, ...]
    note: str
    recommend: bool = True  # 추천에 쓸지 (라이브러리에는 남긴다)

    @property
    def difficulty_rank(self) -> int:
        return DIFFICULTIES.index(self.ai_difficulty)


def _card(raw: dict) -> FormatCard:
    cid = str(raw.get("id", ""))
    if not _ID.match(cid):
        raise FormatCardError(f"카드 id 모양이 틀림: {cid!r} (F-01 꼴)")
    beats_raw = raw.get("beats")
    if not isinstance(beats_raw, list) or len(beats_raw) < 2:
        raise FormatCardError(f"{cid}: 구간(beats)이 두 개 이상이어야 함")
    punch = raw.get("punch_beat")
    if not isinstance(punch, int) or not 0 <= punch < len(beats_raw):
        raise FormatCardError(f"{cid}: punch_beat가 구간 번호 밖")
    beats = []
    for n, b in enumerate(beats_raw):
        if not (isinstance(b, list) and len(b) == 2 and isinstance(b[0], str)
                and isinstance(b[1], int | float) and b[1] > 0):  # fmt: skip
            raise FormatCardError(f"{cid}: 구간은 [이름, 비율] 꼴")
        beats.append(Beat(b[0], float(b[1]), n == punch))
    if abs(sum(b.ratio for b in beats) - 1.0) > 0.01:
        raise FormatCardError(f"{cid}: 구간 비율 합이 1이 아님")
    if raw.get("punchline") not in PUNCHLINES:
        raise FormatCardError(f"{cid}: punchline은 {PUNCHLINES} 중 하나")
    if raw.get("ai_difficulty") not in DIFFICULTIES:
        raise FormatCardError(f"{cid}: ai_difficulty는 {DIFFICULTIES} 중 하나")
    people = raw.get("people")
    if not isinstance(people, int) or people < 0:
        raise FormatCardError(f"{cid}: people은 0 이상 정수")
    recommend = raw.get("recommend", True)
    if not isinstance(recommend, bool):
        raise FormatCardError(f"{cid}: recommend는 true·false")
    if recommend and people > MAX_PEOPLE:
        raise FormatCardError(
            f"{cid}: 한 화면 인물 {people}명 > {MAX_PEOPLE}명이면 recommend = false"
        )
    names = raw.get("audio_names") or []
    return FormatCard(cid, str(raw.get("name") or "").strip() or cid, tuple(beats),
                      str(raw.get("hook") or ""), raw["punchline"], people,
                      raw["ai_difficulty"], tuple(str(x) for x in names),
                      str(raw.get("note") or ""), recommend)  # fmt: skip


def load_cards(path: Path = CARDS_PATH) -> tuple[FormatCard, ...]:
    """카드 파일을 읽고 검사한다. 틀리면 `FormatCardError` (파일이 없으면 OSError)."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    cards = tuple(_card(c) for c in raw.get("cards") or [])
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise FormatCardError("카드 id가 겹침")
    return cards


@lru_cache(maxsize=1)
def cards() -> tuple[FormatCard, ...]:
    """기본 카드 파일 (한 번 읽어 둔다)."""
    return load_cards()


def by_id() -> dict[str, FormatCard]:
    return {c.id: c for c in cards()}
