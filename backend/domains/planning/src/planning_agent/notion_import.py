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

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail  # 바깥 서비스가 준 오류 코드 (예: invalid_client)


@dataclass
class NotionPage:
    title: str
    url: str
    blocks: list[tuple[str, str, bool | None]] = field(default_factory=list)  # (kind, text, done)
    skipped: int = 0


def token() -> str:
    """화면(연결 › 문서 › 노션)에서 넣은 토큰이 먼저, 없으면 서버 환경변수."""
    from planning_agent import notion_store  # 순환 import 피하기

    saved = notion_store.saved_token()
    if saved:
        return saved
    return (os.getenv("PLANNING_NOTION_TOKEN") or os.getenv("NOTION_TOKEN") or "").strip()


@dataclass
class NotionHit:
    id: str
    title: str
    url: str
    edited: str  # 마지막 수정 시각 (ISO)


async def search(query: str, client: httpx.AsyncClient | None = None) -> list[NotionHit]:
    """제목으로 노션 페이지를 찾는다 (통합에 공유된 페이지만 나온다). 최대 10개, 최근 수정 순."""
    key = token()
    if not key:
        raise NotionError(
            "NOTION_NOT_CONFIGURED", "노션이 연결돼 있지 않습니다 (연결 › 문서 › 노션)"
        )
    body = {
        "query": query.strip()[:100],
        "filter": {"property": "object", "value": "page"},
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        "page_size": 10,
    }
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": VERSION}
    own = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        res = await http.post(f"{API}/search", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise NotionError("NOTION_UNREACHABLE", "노션에 연결하지 못했습니다") from exc
    finally:
        if own:
            await http.aclose()
    _raise_for(res)
    hits: list[NotionHit] = []
    for page in res.json().get("results", []):
        if page.get("object") != "page" or page.get("archived"):
            continue
        hits.append(
            NotionHit(
                id=str(page.get("id", "")).replace("-", ""),
                title=_title_of(page),
                url=str(page.get("url") or ""),
                edited=str(page.get("last_edited_time") or ""),
            )
        )
    return hits


async def check(key: str, client: httpx.AsyncClient | None = None) -> str:
    """토큰으로 노션 `users/me`를 불러 연결을 확인한다.
    워크스페이스 이름(없으면 봇 이름)을 돌려준다.
    틀리면 NotionError(NOTION_UNAUTHORIZED), 닿지 않으면 NotionError(NOTION_UNREACHABLE)."""
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": VERSION}
    own = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        res = await http.get(f"{API}/users/me", headers=headers)
    except httpx.HTTPError as exc:
        raise NotionError("NOTION_UNREACHABLE", "노션에 연결하지 못했습니다") from exc
    finally:
        if own:
            await http.aclose()
    _raise_for(res)
    me = res.json()
    bot = me.get("bot") or {}
    return str(bot.get("workspace_name") or me.get("name") or "노션")


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


MAX_TABLES = 20  # 표 행을 따로 읽는 표 수 상한 (노션 호출 수를 묶는다)


def _cell(rich: list[dict[str, Any]]) -> str:
    """표 칸 글: 마크다운 표가 깨지지 않게 `|`·줄바꿈을 바꾼다."""
    return _text(rich).replace("|", "\\|").replace("\n", " ")


def _table_md(rows: list[dict[str, Any]]) -> str:
    """노션 표 행(table_row) → 마크다운 표. 첫 행을 머리글로 쓴다."""
    grid = [[_cell(c) for c in (r.get("table_row") or {}).get("cells", [])] for r in rows]
    grid = [g for g in grid if g]
    if not grid:
        return ""
    width = max(len(g) for g in grid)
    grid = [g + [""] * (width - len(g)) for g in grid]
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(g) + " |" for g in grid[1:]]
    return "\n".join(lines)


def _url_of(body: dict[str, Any]) -> str:
    for k in ("external", "file"):
        u = (body.get(k) or {}).get("url")
        if u:
            return str(u)
    return str(body.get("url") or "")


def convert(blocks: list[dict[str, Any]]) -> tuple[list[tuple[str, str, bool | None]], int]:
    """노션 블록 → (kind, text, done). kind는 h2·p·todo·ref, p는 마크다운 글이다.
    표는 마크다운 표로(행은 fetch가 `_rows`에 붙여 둔다), 코드는 ``` 블록으로, 이미지·파일·북마크는
    링크로 옮긴다. 하위 블록(토글 안·목록 안)과 못 옮기는 블록은 건너뛰고 그 수를 돌려준다."""
    out: list[tuple[str, str, bool | None]] = []
    skipped = 0
    number = 0
    for b in blocks:
        t = b.get("type", "")
        body = b.get(t) or {}
        rich = body.get("rich_text", [])
        text = _text(rich)
        if t != "numbered_list_item":
            number = 0
        if t in ("heading_1", "heading_2", "heading_3"):
            if text:
                out.append(("h2", text, None))
        elif t == "paragraph":
            if text:
                out.append(("p", text, None))
        elif t in ("quote", "callout"):
            if text:
                icon = ((body.get("icon") or {}).get("emoji") or "") if t == "callout" else ""
                out.append(("p", f"> {icon + ' ' if icon else ''}{text}", None))
        elif t == "to_do":
            out.append(("todo", text, bool(body.get("checked"))))
        elif t == "bulleted_list_item":
            out.append(("p", f"- {text}", None))
        elif t == "numbered_list_item":
            number += 1
            out.append(("p", f"{number}. {text}", None))
        elif t == "toggle":
            out.append(("p", f"▸ {text}", None))
            skipped += 1 if b.get("has_children") else 0
        elif t == "code":
            lang = str(body.get("language") or "").replace("plain text", "")
            out.append(("p", f"```{lang}\n{text}\n```", None))
        elif t == "table":
            md = _table_md(b.get("_rows") or [])
            if md:
                out.append(("p", md, None))
            else:
                skipped += 1
        elif t in ("image", "file", "pdf", "video", "audio", "bookmark", "embed", "link_preview"):
            link = _url_of(body)
            if not link.startswith(("https://", "http://")):
                skipped += 1
                continue
            caption = _text(body.get("caption", [])) or {"image": "이미지", "file": "파일",
                "pdf": "PDF", "video": "영상", "audio": "소리"}.get(t, link)  # fmt: skip
            out.append(("p", f"[{caption}]({link})", None))
        elif t == "equation":
            out.append(("p", f"`{body.get('expression', '')}`", None))
        elif t == "child_page":
            out.append(("ref", f"하위 페이지: {body.get('title', '')}", None))
        elif t == "divider":
            out.append(("p", "---", None))
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
        # 표는 행이 하위 블록이라 표마다 한 번 더 읽는다 (상한 MAX_TABLES)
        tables = [b for b in raw[:MAX_BLOCKS] if b.get("type") == "table" and b.get("has_children")]
        for b in tables[:MAX_TABLES]:
            res = await http.get(
                f"{API}/blocks/{b['id']}/children", headers=headers, params={"page_size": 100}
            )
            _raise_for(res)
            b["_rows"] = res.json().get("results", [])
    except httpx.HTTPError as exc:
        raise NotionError("NOTION_UNREACHABLE", "노션에 연결하지 못했습니다") from exc
    finally:
        if own:
            await http.aclose()
    blocks, skipped = convert(raw[:MAX_BLOCKS])
    return NotionPage(
        title=_title_of(page), url=page.get("url") or url, blocks=blocks, skipped=skipped
    )
