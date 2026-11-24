"""요청마다 회사 고르기 (5단계): 서버가 meta.tenant_id를 그 요청의 회사로 걸어 준다."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus


@pytest.fixture(autouse=True)
def _companies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"company_id": cid, "name": cid, "created_at": "2026-11-20T00:00:00Z"}
        for cid in ("catbooks", "nubitour", "studyon")
    ]
    path = tmp_path / "companies.json"
    path.write_text(json.dumps({"companies": rows}), encoding="utf-8")
    monkeypatch.setenv("AX_COMPANIES_FILE", str(path))
    company_scope.reset_for_tests()


async def _who(request: AgentRequest) -> AgentResult:
    """핸들러는 감싸지 않는다. 띄운 작업에서도 같은 회사를 읽는지 본다."""
    spawned = await asyncio.create_task(asyncio.sleep(0, result=company_scope.active()))
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output={"company": company_scope.active(), "spawned": spawned},
    )


@pytest.fixture
def client(card: Any, handlers: dict[str, Any]) -> TestClient:
    return TestClient(create_agent_app(card, {**handlers, "echo": _who}))


def _company(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    result = client.post("/a2a", json=body).json()
    assert result["status"] == "completed"
    return result["output"]


@pytest.mark.parametrize("tenant", ["catbooks", "nubitour", "studyon"])
def test_server_sets_request_company_for_handler_and_spawned_task(
    client: TestClient, request_body: Any, tenant: str
) -> None:
    out = _company(client, request_body(tenant_id=tenant))
    assert out == {"company": tenant, "spawned": tenant}
    assert company_scope.active() is None  # 요청이 끝나면 풀린다


def test_alias_and_missing_tenant_are_catbooks(
    client: TestClient, request_body: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AX_DEFAULT_COMPANY", raising=False)
    assert _company(client, request_body(tenant_id="shop-demo"))["company"] == "catbooks"
    body = request_body()
    body["meta"].pop("tenant_id", None)
    assert _company(client, body)["company"] == "catbooks"


def test_unknown_company_is_not_rejected_and_warns_once(
    client: TestClient, request_body: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="ax.company_scope"):
        first = _company(client, request_body(tenant_id="ghost-co"))
        _company(client, request_body(tenant_id="ghost-co"))
    assert first["company"] == "ghost-co"  # 거절하지 않고, 다른 회사와 섞지도 않는다
    warned = [r for r in caplog.records if "ghost-co" in r.getMessage()]
    assert len(warned) == 1


def test_bad_tenant_value_falls_back_to_catbooks_with_warning(
    client: TestClient, request_body: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="ax.company_scope"):
        out = _company(client, request_body(tenant_id="Not A Company!"))
    assert out["company"] == "catbooks"
    assert any("맞지 않아" in r.getMessage() for r in caplog.records)


def test_visible_and_using_outside_and_inside_a_request() -> None:
    assert company_scope.active() is None
    assert company_scope.visible("nubitour") and company_scope.visible(None)  # 요청 밖은 전부
    with company_scope.using("nubitour") as cid:
        assert cid == "nubitour" == company_scope.active()
        assert company_scope.visible("nubitour")
        assert not company_scope.visible("catbooks")
        assert not company_scope.visible("shop-demo")  # 별칭 = 캣북스
        with company_scope.using(None):
            assert company_scope.active() == "catbooks"
        assert company_scope.active() == "nubitour"
    assert company_scope.active() is None


def test_scoped_wrapper_still_works() -> None:
    class _Meta:
        tenant_id = "studyon"

    class _Req:
        meta = _Meta()

    async def handler(_: Any) -> str | None:
        return company_scope.active()

    assert asyncio.run(company_scope.scoped(handler)(_Req())) == "studyon"


def test_prompt_line_reads_name_and_industry_from_company_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"company_id": "nubitour", "name": "누비투어", "industry": "여행",
         "created_at": "2026-11-20T00:00:00Z"},
        {"company_id": "studyon", "name": "스터디\n온", "created_at": "2026-11-20T00:00:00Z"},
    ]  # fmt: skip
    path = tmp_path / "companies2.json"
    path.write_text(json.dumps({"companies": rows}), encoding="utf-8")
    monkeypatch.setenv("AX_COMPANIES_FILE", str(path))
    assert company_scope.prompt_line() == ""  # 요청 밖은 회사를 모른다
    with company_scope.using("nubitour"):
        assert company_scope.prompt_line() == (
            "이 회사: 누비투어 (업종: 여행). "
            "파는 것은 '여행 상품', 고객이 사는 일은 '예약'(이)라고 부른다\n"
        )
        assert company_scope.terms().order == "예약"
    with company_scope.using("studyon"):  # 업종이 없으면 지어내지 않고, 줄바꿈은 지운다
        assert company_scope.prompt_line() == "이 회사: 스터디 온\n"
    with company_scope.using("catbooks"):  # 회사 목록에 없는 회사
        assert company_scope.prompt_line() == ""


def test_industry_terms_follow_the_choice_or_words_in_free_text() -> None:
    from ax_agent_template.industry_terms import DEFAULT, INDUSTRIES, terms_for

    assert terms_for("여행사").order == "예약"
    assert terms_for("온라인 여행 예약 플랫폼").item == "여행 상품"  # 자유 입력도 낱말로 맞춘다
    assert terms_for("교육").item == "강의" and terms_for("에듀테크").order == "수강 신청"
    assert terms_for("서점·출판").item == "책"
    for unknown in ("", None, "기타", "온라인 쇼핑몰", "우주 항만"):
        assert terms_for(unknown) == DEFAULT  # 모르면 지어내지 않고 기본 낱말
    assert INDUSTRIES[-1] == "기타" and len(set(INDUSTRIES)) == len(INDUSTRIES)
    assert company_scope.terms() == DEFAULT  # 요청 밖
