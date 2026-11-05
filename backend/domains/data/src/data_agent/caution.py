"""영상을 광고·홍보 재료로 쓸 때 확인할 것 (v2 화면 칸 `ad_usable`·`caution_types`). 코드 규칙만.

- real_person(초상권): 분류기가 소재 이름 자체를 실존 사람으로 본 것 (`is_person`)
- trademark(상표권): 실존 고유 이름(팀·리그·상표·작품 제목)이 들었고 사람은 아닌 것
- music(저작권): 분류가 K-pop·음악 (노래·음원·뮤직비디오)
- ad_usable: 확인할 것이 하나라도 있으면 false. 없고 분류·실존 이름 판단을 모두 알 때만 true.
  모르면 None + 이유 (지어내지 않는다)

영상 분류기는 칸(분류)만 정하고 실존 이름은 따로 보지 않는다. 그래서
- 인사이트·기회 카드의 근거 영상은 그 주제의 분류 결과를 따른다 (`judge`)
- 카테고리 상위 영상(`top_videos`)은 제목에 지금 인기 주제의 실존 이름이 들었을 때만 이름 쪽을
  정하고, 아니면 모름이다 (`for_title`)
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ax_contracts.schemas.data import CautionType, PopularVideo, VideoLink

MUSIC_CATEGORY = "K-pop·음악"
LABEL = {"real_person": "실존 인물", "trademark": "상표", "music": "음원"}


@dataclass(frozen=True)
class Caution:
    ad_usable: bool | None
    types: list[CautionType] = field(default_factory=list)
    reason: str | None = None


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def judge(
    category: str | None,
    *,
    real_entity: bool | None,
    is_person: bool | None,
    names: Sequence[str] = (),
    basis: str = "",
) -> Caution:
    """분류 결과 하나 → 확인할 것. `basis`는 이유 문장 앞머리 (예: 주제 '리그오브레전드' 분류)."""
    types: list[CautionType] = []
    if is_person:
        types.append("real_person")
    if real_entity and not is_person:
        types.append("trademark")
    if category == MUSIC_CATEGORY:
        types.append("music")
    head = f"{basis}: " if basis else ""
    if types:
        named = f" ({', '.join(names[:3])})" if names and (is_person or real_entity) else ""
        return Caution(False, types,
                       f"{head}{'·'.join(LABEL[t] for t in types)} 확인 필요{named}")  # fmt: skip
    if not category or category == "unknown":
        return Caution(None, [], f"{head}분류를 몰라 확인할 것을 정할 수 없음")
    if real_entity is None:
        return Caution(None, [], f"{head}실존 인물·상표 판단이 없음")
    return Caution(True, [], None)


def for_title(
    title: str | None,
    category: str | None,
    topics: Iterable[tuple[Sequence[str], bool | None]],
) -> Caution:
    """카테고리 상위 영상: 음원은 영상 분류로, 실존 이름은 제목에 인기 주제의 이름이 들었을 때만.

    `topics` = (실존 이름들, 사람인지) 목록 (지금 인기 주제의 분류 결과).
    """
    text = _norm(title or "")
    person = brand = False
    hits: list[str] = []
    for names, is_person in topics:
        for name in names:
            if len(_norm(name)) >= 2 and _norm(name) in text:
                hits.append(name)
                person = person or bool(is_person)
                brand = brand or not is_person
    types: list[CautionType] = []
    if person:
        types.append("real_person")
    if brand:
        types.append("trademark")
    if category == MUSIC_CATEGORY:
        types.append("music")
    if types:
        named = f" ({', '.join(dict.fromkeys(hits))})" if hits else ""
        return Caution(False, types, f"{'·'.join(LABEL[t] for t in types)} 확인 필요{named}")
    if not category or category == "unknown":
        return Caution(None, [], "영상 분류를 몰라 확인할 것을 정할 수 없음")
    return Caution(None, [], "영상 분류는 실존 인물·상표를 따로 보지 않음 (제목에 지금 인기 주제의"
                             " 실존 이름 없음, 직접 확인 필요)")  # fmt: skip


def apply[V: (VideoLink, PopularVideo)](model: V, caution: Caution) -> V:
    """VideoLink·PopularVideo에 칸을 채운 사본."""
    update: dict[str, Any] = {
        "ad_usable": caution.ad_usable,
        "caution_types": list(caution.types),
        "caution_reason": caution.reason,
    }
    return model.model_copy(update=update)
