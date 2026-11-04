"""노션 페이지를 정리 문서 블록으로 가져온다 (읽기만).

- 토큰은 `PLANNING_NOTION_TOKEN`(없으면 `NOTION_TOKEN`). 노션 설정 → 연결 → 통합에서 만들고,
  가져올 페이지를 그 통합에 공유해야 읽힌다 (노션 정책).
- 제목·문단·할 일·글머리·번호 목록·인용·콜아웃만 옮긴다. 표·이미지·코드·하위 페이지는 건너뛰고
  건너뛴 수를 돌려준다.
- 토큰 값은 기록하지 않는다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_BLOCKS = 500  # 한 번에 가져오는 블록 상한 (너무 긴 페이지는 앞부분만)

_ID = re.compile(
    r"([0-9a-fA-F]{32})|([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class NotionError(Exception):
    """가져오기 실패. `code`는 화면에 그대로 보여도 되는 짧은 이유."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class NotionPage:
    title: str
    url: str
    blocks: list[tuple[str, str, bool | None]] = field(default_factory=list)  # (kind, text, done)
    skipped: int = 0


def token() -> str:
    return (os.getenv("PLANNING_NOTION_TOKEN") or os.getenv("NOTION_TOKEN") or "").strip()


def page_id(url: str) -> str:
    """노션 주소(또는 ID)에서 페이지 ID를 꺼낸다. 주소 끝의 32자리가 페이지 ID다."""
    path = url.split("?")[0].split("#")[0]
    found = _ID.findall(path)
    if not found:
        raise NotionError("NOTION_BAD_URL", "노션 페이지 주소에서 페이지 ID를 찾지 못했습니다")
    raw = next(a or b for a, b in reversed(found))
    return raw.replace("-", "")


def _text(rich: list[dict[str, Any]]) -> str:
    return "".join(r.get("plain_text", "") for r in rich or []).strip()


def convert(blocks: list[dict[str, Any]]) -> tuple[list[tuple[str, str, bool | None]], int]:
    """노션 블록 → (kind, text, done). kind는 h2·p·todo. 못 옮기는 블록 수도 돌려준다."""
    out: list[tuple[str, str, bool | None]] = []
    skipped = 0
    number = 0
    for b in blocks:
        t = b.get("type", "")
        body = b.get(t) or {}
        rich = body.get("rich_text", [])
        if t != "numbered_list_item":
            number = 0
        if t in ("heading_1", "heading_2", "heading_3"):
            text = _text(rich)
            if text:
                out.append(("h2", text, None))
        elif t in ("paragraph", "quote", "callout"):
            text = _text(rich)
            if text:
                out.append(("p", text, None))
        elif t == "to_do":
            out.append(("todo", _text(rich), bool(body.get("checked"))))
        elif t == "bulleted_list_item":
            out.append(("p", f"• {_text(rich)}", None))
        elif t == "numbered_list_item":
            number += 1
            out.append(("p", f"{number}. {_text(rich)}", None))
        elif t in ("divider",):
            continue
        else:
            skipped += 1
    return out, skipped


def _title_of(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            text = _text(prop.get("title", []))
            if text:
                return text
    return "노션에서 가져온 문서"


def _raise_for(res: httpx.Response) -> None:
    if res.status_code in (401,):
        raise NotionError("NOTION_UNAUTHORIZED", "노션 토큰이 맞지 않습니다")
    if res.status_code in (403, 404):
        raise NotionError(
            "NOTION_NOT_SHARED",
            "페이지를 찾지 못했습니다. "
            "노션에서 이 페이지를 통합(integration)에 공유했는지 확인하세요",
        )
    if res.status_code >= 400:
        raise NotionError("NOTION_FAILED", f"노션 응답 {res.status_code}")


async def fetch(url: str, client: httpx.AsyncClient | None = None) -> NotionPage:
    """노션 페이지 제목과 블록을 읽는다. 토큰이 없으면 NotionError."""
    key = token()
    if not key:
        raise NotionError(
            "NOTION_NOT_CONFIGURED", "노션 토큰(PLANNING_NOTION_TOKEN)이 없어 가져올 수 없습니다"
        )
    pid = page_id(url)
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": VERSION}
    own = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        res = await http.get(f"{API}/pages/{pid}", headers=headers)
        _raise_for(res)
        page = res.json()
        raw: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(raw) < MAX_BLOCKS:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            res = await http.get(f"{API}/blocks/{pid}/children", headers=headers, params=params)
            _raise_for(res)
            body = res.json()
            raw.extend(body.get("results", []))
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
    except httpx.HTTPError as exc:
        raise NotionError("NOTION_UNREACHABLE", "노션에 연결하지 못했습니다") from exc
    finally:
        if own:
            await http.aclose()
    blocks, skipped = convert(raw[:MAX_BLOCKS])
    return NotionPage(
        title=_title_of(page), url=page.get("url") or url, blocks=blocks, skipped=skipped
    )
