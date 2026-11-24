"""유튜브 밈 키워드 (11/24): 데이터 AI가 하루 1번 쇼츠를 찾을 회사 밈 키워드 (예: 밈·공감·짤).

- `GET  /api/dashboard/youtube-meme-keywords`: 지금 쓰는 목록과 출처
  (`screen` 화면 저장 · `tenant` 회사 설정 trends.toml [youtube] meme_keywords · `none` 없음)
- `POST /api/dashboard/youtube-meme-keywords/save`: 목록 저장(사람 토큰). 앞뒤 공백을 떼고 중복은
  하나로, 최대 10개·한 개 30자까지
- `POST /api/dashboard/youtube-meme-keywords/clear`: 화면 저장 목록을 지우고 회사 설정 파일로

저장 파일은 `ax_agent_template.tenant.yt_meme_keywords_file()`(회사 연결 파일 옆).
data 쇼츠 검색(`data_agent.meme_shorts`)이 같은 파일을 읽는다. 키워드 하나가 search 100단위다.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ax_agent_template import tenant
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok

MAX_KEYWORDS = 10
MAX_CHARS = 30

router = APIRouter(prefix="/api/dashboard/youtube-meme-keywords",
                   dependencies=[Depends(require_human)])  # fmt: skip


class SaveIn(BaseModel):
    keywords: list[str] = Field(max_length=100)


def _tenant_keywords() -> list[str]:
    path = tenant.setting("DATA_TREND_CONFIG")
    if not path or not Path(path).is_file():
        return []
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("youtube") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return []
    listed = raw.get("meme_keywords", []) if isinstance(raw, dict) else []
    return [str(w).strip() for w in listed if str(w).strip()]


def _state() -> dict[str, Any]:
    saved = tenant.yt_meme_keywords()
    from_tenant = _tenant_keywords()
    if saved is not None:
        source, words = "screen", saved
    elif from_tenant:
        source, words = "tenant", from_tenant
    else:
        source, words = "none", []
    return {
        "keywords": words[:MAX_KEYWORDS],
        "source": source,
        "tenant_count": len(from_tenant),
        "max": MAX_KEYWORDS,
        "editable": tenant.yt_meme_keywords_file() is not None,
    }


def _clean(raw: list[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        word = " ".join(item.split())
        if not word:
            continue
        if len(word) > MAX_CHARS:
            raise ApiError(422, "TOO_LONG", f"키워드는 {MAX_CHARS}자까지예요: {word[:40]}")
        if word.lower() not in {w.lower() for w in out}:
            out.append(word)
    if len(out) > MAX_KEYWORDS:
        raise ApiError(422, "TOO_MANY", f"밈 키워드는 {MAX_KEYWORDS}개까지예요 ({len(out)}개)")
    return out


def _path() -> Path:
    path = tenant.yt_meme_keywords_file()
    if path is None:
        raise ApiError(409, "KEYWORDS_NOT_EDITABLE", "회사 연결 파일 설정이 없어 저장할 수 없다")
    return path


@router.get("")
def get_keywords() -> dict:
    return ok(_state())


@router.post("/save")
def save_keywords(body: SaveIn) -> dict:
    path = _path()
    words = _clean(body.keywords)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"keywords": words}, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)
    return ok(_state())


@router.post("/clear")
def clear_keywords() -> dict:
    _path().unlink(missing_ok=True)
    return ok(_state())
