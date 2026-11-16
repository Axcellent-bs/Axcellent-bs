"""캣북스 장르 코드 = 판매 서버 장르 코드 (11/16).

기준: Catbooks-demo/catbooks-demo `backend/shop-api/src/ax_shop_api/genres.py` GENRES
(커밋 413a5584).
- 회사 카탈로그 `[sales.genres]`가 그 16개 코드·이름·순서와 같다
- 판매 서버 확정 전 잠정 코드(`ai_it`·`notes`)가 data 코드·카탈로그·테스트·평가 자료에 남지 않았다
- 가짜 판매 출처 설정(데모 전용)은 회사 카탈로그에 있는 장르 코드만 쓴다
"""

from __future__ import annotations

import re
from pathlib import Path

from data_agent import catalog
from data_agent.sales_fake import FAKE_DIR, load_config

# 판매 서버 genres.py GENRES (커밋 413a5584) 순서 그대로
SHOP_GENRES: list[tuple[str, str]] = [
    ("novel", "소설"),
    ("essay", "시·에세이"),
    ("business", "경제경영"),
    ("self_help", "자기계발"),
    ("humanities", "인문"),
    ("society", "사회·정치"),
    ("history", "역사"),
    ("science", "과학"),
    ("it", "AI·IT"),
    ("language", "외국어"),
    ("kids", "어린이"),
    ("comics", "만화"),
    ("hobby", "여행·취미"),
    ("writing", "필기구"),
    ("notebook", "노트"),
    ("deco", "다꾸"),
]

DATA = Path(__file__).resolve().parents[1]  # backend/domains/data
SCANNED = ("src", "tests", "eval")
SUFFIXES = {".py", ".toml", ".json", ".jsonl"}
OLD_IT = re.compile(r"\bai_it\b")
# 잠정 코드 notes: toml 키·값, 또는 다른 장르 코드나 "genre"와 같은 줄에 따옴표로 쓴 "notes"
NOTES_TOML = re.compile(r"^\s*notes\s*=|[\"']notes[\"']", re.MULTILINE)
NOTES_QUOTED = re.compile(r"[\"']notes[\"']")
GENRE_HINT = re.compile(
    r"genre|[\"'](?:" + "|".join(code for code, _ in SHOP_GENRES) + r")[\"']"
)


def _sales():
    sales = catalog.load_catalog("catbooks").sales
    assert sales is not None
    return sales


def test_catbooks_genres_are_the_shop_server_genres() -> None:
    assert list(_sales().genres.items()) == SHOP_GENRES


def test_catalog_genre_tables_point_at_known_genres() -> None:
    sales = _sales()
    assert set(sales.genre_keywords) <= set(sales.genres)
    assert {g.genre_code for g in sales.genre_external} <= set(sales.genres)


def _files() -> list[Path]:
    me = Path(__file__).resolve()
    return sorted(
        p
        for folder in SCANNED
        for p in (DATA / folder).rglob("*")
        if p.is_file() and p.suffix in SUFFIXES and p.resolve() != me
        and "__pycache__" not in p.parts
    )  # fmt: skip


def test_no_provisional_genre_code_left() -> None:
    files = _files()
    assert any(p.name == "catbooks.toml" for p in files)  # 카탈로그도 본다
    found: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if OLD_IT.search(line):
                found.append(f"{path.relative_to(DATA)}:{n} ai_it")
            if path.suffix == ".toml":
                if NOTES_TOML.search(line):
                    found.append(f"{path.relative_to(DATA)}:{n} notes")
            elif NOTES_QUOTED.search(line) and GENRE_HINT.search(line):
                found.append(f"{path.relative_to(DATA)}:{n} notes")
    assert not found, "잠정 장르 코드가 남아 있음 (it·notebook으로 바꿀 것):\n" + "\n".join(found)


def test_the_scan_finds_a_planted_old_code() -> None:
    assert OLD_IT.search('genre_code = "ai_it"') and NOTES_TOML.search("notes = 0.6")
    line = 'assert set(sales.genres) == {"writing", "notes", "deco"}'
    assert NOTES_QUOTED.search(line) and GENRE_HINT.search(line)
    # 장르와 상관없는 notes 칸(예: 결과의 "notes" 목록)은 잡지 않는다
    other = 'return {"cells": cells, "notes": notes}'
    assert not (NOTES_QUOTED.search(other) and GENRE_HINT.search(other))


def test_fake_catalog_uses_only_catbooks_genres() -> None:
    genres = set(_sales().genres)
    fake = load_config(FAKE_DIR / "catbooks.toml")
    assert set(fake.genre_weights) <= genres
    assert {g for pref in fake.preferences for g in pref.boost} <= genres
    assert {p.genre_code for p in fake.products} <= genres
