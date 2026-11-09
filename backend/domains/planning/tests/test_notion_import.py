"""노션 페이지를 정리 문서로 가져온다 (`import_notion`). 실제 노션은 부르지 않는다."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ax_contracts.schemas import ExecuteDecision
from planning_agent import notion_import
from planning_agent.domain.service import ActionError, PlanningService

PID = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _rich(text: str) -> list[dict]:
    return [{"plain_text": text}]


BLOCKS = [
    {"type": "heading_2", "heading_2": {"rich_text": _rich("배경")}},
    {"type": "paragraph", "paragraph": {"rich_text": _rich("결제 실패 문의가 늘었다")}},
    {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich("카드")}},
    {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rich("첫째")}},
    {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rich("둘째")}},
    {"type": "to_do", "to_do": {"rich_text": _rich("담당 정하기"), "checked": True}},
    {"type": "image", "image": {}},
    {"type": "divider", "divider": {}},
]


@pytest.mark.role("planning")
def test_page_id_from_url_and_dashed_id() -> None:
    assert notion_import.page_id(f"https://www.notion.so/team/결제-정리-{PID}?pvs=4") == PID
    dashed = f"{PID[:8]}-{PID[8:12]}-{PID[12:16]}-{PID[16:20]}-{PID[20:]}"
    assert notion_import.page_id(dashed) == PID
    with pytest.raises(notion_import.NotionError):
        notion_import.page_id("https://www.notion.so/없는-주소")


@pytest.mark.role("planning")
def test_convert_blocks() -> None:
    out, skipped = notion_import.convert(BLOCKS)
    assert out == [
        ("h2", "배경", None),
        ("p", "결제 실패 문의가 늘었다", None),
        ("p", "- 카드", None),
        ("p", "1. 첫째", None),
        ("p", "2. 둘째", None),
        ("todo", "담당 정하기", True),
        ("p", "---", None),
    ]
    assert skipped == 1  # 주소 없는 이미지


@pytest.mark.role("planning")
def test_convert_table_code_link_and_quote() -> None:
    def row(*cells: str) -> dict:
        return {"type": "table_row", "table_row": {"cells": [_rich(c) for c in cells]}}

    blocks = [
        {"type": "table", "table": {}, "has_children": True,
         "_rows": [row("상품", "가격"), row("밤의 도서관", "16,800원"), row("a|b", "x")]},
        {"type": "code", "code": {"rich_text": _rich("print(1)"), "language": "python"}},
        {"type": "image", "image": {"external": {"url": "https://img.example/a.png"},
                                    "caption": _rich("표지")}},
        {"type": "bookmark", "bookmark": {"url": "https://catbooks.example"}},
        {"type": "callout", "callout": {"rich_text": _rich("주의"), "icon": {"emoji": "💡"}}},
        {"type": "image", "image": {"external": {"url": "javascript:alert(1)"}}},
    ]  # fmt: skip
    out, skipped = notion_import.convert(blocks)
    table = "\n".join(
        ["| 상품 | 가격 |", "|---|---|", "| 밤의 도서관 | 16,800원 |", r"| a\|b | x |"]
    )
    assert out[0] == ("p", table, None)
    assert out[1] == ("p", "```python\nprint(1)\n```", None)
    assert out[2] == ("p", "[표지](https://img.example/a.png)", None)
    assert out[3] == ("p", "[https://catbooks.example](https://catbooks.example)", None)
    assert out[4] == ("p", "> 💡 주의", None)
    assert skipped == 1  # javascript: 주소는 옮기지 않는다


@pytest.mark.role("planning")
def test_fetch_reads_title_and_blocks_with_paging(monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_NOTION_TOKEN", "secret_test")
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        assert req.headers["Authorization"] == "Bearer secret_test"
        if req.url.path.endswith(f"/pages/{PID}"):
            title = {"t": {"type": "title", "title": _rich("결제 정리")}}
            return httpx.Response(200, json={"url": "https://notion.so/x", "properties": title})
        if "start_cursor" not in req.url.params:
            return httpx.Response(
                200, json={"results": BLOCKS[:3], "has_more": True, "next_cursor": "c2"}
            )
        return httpx.Response(200, json={"results": BLOCKS[3:], "has_more": False})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await notion_import.fetch(PID, client=c)

    page = asyncio.run(go())
    assert page.title == "결제 정리" and len(page.blocks) == 7 and page.skipped == 1
    assert len(calls) == 3


@pytest.mark.role("planning")
def test_fetch_needs_token_and_explains_not_shared(monkeypatch) -> None:
    monkeypatch.delenv("PLANNING_NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(notion_import.NotionError) as e:
        asyncio.run(notion_import.fetch(PID))
    assert e.value.code == "NOTION_NOT_CONFIGURED"
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")

    async def go():
        tr = httpx.MockTransport(lambda r: httpx.Response(404, json={}))
        async with httpx.AsyncClient(transport=tr) as c:
            return await notion_import.fetch(PID, client=c)

    with pytest.raises(notion_import.NotionError) as e:
        asyncio.run(go())
    assert e.value.code == "NOTION_NOT_SHARED"


@pytest.mark.role("planning")
def test_import_action_saves_doc() -> None:
    svc = PlanningService(gate=_gate, relater=None)

    async def fake(url: str) -> notion_import.NotionPage:
        blocks, skipped = notion_import.convert(BLOCKS)
        return notion_import.NotionPage("결제 정리", "https://notion.so/x", blocks, skipped)

    svc.notion_fetch = fake
    out = asyncio.run(svc.dispatch({"type": "importNotion", "url": PID})).output
    doc = next(x for x in svc.state.docs if x.id == out["doc_id"])
    assert doc.title == "결제 정리"
    assert doc.blocks[0].kind == "ref" and "notion.so/x" in doc.blocks[0].text
    assert [b.kind for b in doc.blocks[1:]] == ["h2", "p", "p", "p", "p", "todo", "p"]
    assert doc.blocks[-2].done is True and doc.blocks[-1].text == "---" and out["skipped"] == 1


@pytest.mark.role("planning")
def test_import_action_error_becomes_action_error() -> None:
    svc = PlanningService(gate=_gate, relater=None)

    async def fail(url: str):
        raise notion_import.NotionError("NOTION_NOT_SHARED", "공유 안 됨")

    svc.notion_fetch = fail
    before = len(svc.state.docs)
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "importNotion", "url": PID}))
    assert len(svc.state.docs) == before
