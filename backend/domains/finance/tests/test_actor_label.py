"""누가 했나 표시(`by_label`): 공용 토큰이면 "<화면 계정 이름>(<접속 IP>)"를 같이 남긴다.

모든 화면이 사람 토큰 하나("공용")를 같이 써서 기록이 "공용님이 뺌"으로만 보였다(2026-11-12).
`by`는 그대로 두고(권한·예전 기록) 표시용 `by_label`만 더한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from finance_agent import handlers
from finance_agent.human import actor_label, actor_name, client_ip, label_fields
from finance_agent.main import app
from finance_agent.sources import MockSources

SHARED = {"Authorization": "Bearer tok-shared"}  # 이름: 공용
PERSONAL = {"Authorization": "Bearer human:taeyoon"}
KB_LIKE = "거래일자,적요,출금,입금액,잔액\n2026.10.20,PG정산,,1000,5000\n".encode("cp949")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> Iterator[None]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("FINANCE_HUMAN_TOKENS", "공용:tok-shared")
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("FINANCE_PG_INBOX", raising=False)
    handlers.reset_state(MockSources())
    yield
    handlers.reset_state(MockSources())


def _req(headers: dict[str, str], host: str | None = "10.0.0.9") -> SimpleNamespace:
    lowered = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, d=None: lowered.get(k.lower(), d)),
        client=SimpleNamespace(host=host) if host else None,
    )


def _as(name: str | None, ip: str = "192.168.0.22") -> dict[str, str]:
    """화면이 보내는 머리: 계정 이름은 UTF-8 퍼센트 인코딩, IP는 nginx가 붙인 X-Real-IP."""
    extra = {"X-Ax-Actor-Name": quote(name)} if name else {}
    return SHARED | extra | {"X-Real-IP": ip}


def test_actor_name_is_decoded_cleaned_and_cut() -> None:
    assert actor_name(quote("관리자")) == "관리자"
    assert (
        actor_name(quote("김 (재무)\n팀")) == "김 재무 팀"
    )  # 괄호·줄바꿈은 빼서 "이름(IP)" 모양을 지킨다
    assert actor_name(quote("가" * 60)) == "가" * 40
    assert actor_name("") is None and actor_name(quote("()")) is None


def test_client_ip_prefers_proxy_headers_then_peer() -> None:
    assert client_ip(_req({"X-Real-IP": "192.168.0.22"})) == "192.168.0.22"
    assert client_ip(_req({"X-Forwarded-For": "203.0.113.5, 10.0.0.1"})) == "203.0.113.5"
    assert client_ip(_req({"X-Real-IP": "not-an-ip"})) == "10.0.0.9"  # 이상한 값은 건너뛴다
    assert client_ip(_req({}, host=None)) is None


def test_label_only_for_shared_token() -> None:
    req = _req({"X-Ax-Actor-Name": quote("관리자"), "X-Real-IP": "192.168.0.22"})
    assert actor_label("공용", req) == "관리자(192.168.0.22)"
    assert actor_label("공용", _req({})) == "공용(10.0.0.9)"  # 이름을 안 보내면 공용(IP)
    assert actor_label("공용", _req({}, host=None)) == "공용(IP 모름)"
    assert actor_label("taeyoon", req) is None and label_fields("taeyoon", req) == {}
    assert label_fields("공용", req, "canceled_by_label") == {
        "canceled_by_label": "관리자(192.168.0.22)"
    }


def test_bank_import_and_remove_record_the_label() -> None:
    client = TestClient(app)
    url = "/api/finance/bank/file"
    up = client.post(url, params={"name": "test_bank.csv"}, content=KB_LIKE, headers=_as("관리자"))
    assert up.status_code == 200, up.text
    record = up.json()["meta"]["import"]
    assert (record["by"], record["by_label"]) == ("공용", "관리자(192.168.0.22)")
    removed = client.post(
        f"/api/finance/bank/imports/{record['import_id']}/remove",
        json={"reason": "시험 파일"},
        headers=_as("일반회원", "192.168.0.31"),
    ).json()["data"]
    assert (removed["by"], removed["by_label"]) == ("공용", "일반회원(192.168.0.31)")
    imports = client.get("/api/finance/exports").json()["data"]["bank_imports"]
    original = next(
        i for i in imports if i.get("import_id") == record["import_id"] and i["source"] == "upload"
    )
    assert original["removed"]["by_label"] == "일반회원(192.168.0.31)"


def test_pg_clear_other_pnl_and_closing_record_the_label() -> None:
    client = TestClient(app)
    cleared = client.post("/api/finance/pg/clear", json={"reason": "시연 끝"}, headers=_as(None))
    assert cleared.json()["data"]["by_label"] == "공용(192.168.0.22)"
    pnl = client.post(
        "/api/finance/other-pnl", json={"month": "2026-10", "salary": 1000}, headers=_as("관리자")
    ).json()["data"]["entry"]
    assert (pnl["by"], pnl["by_label"]) == ("공용", "관리자(192.168.0.22)")
    body = {"period": "day", "date": "2026-10-22"}
    report = client.post("/api/finance/closing/confirm", json=body, headers=_as("관리자")).json()
    assert report["data"]["confirmed_by"] == "공용"
    assert report["data"]["confirmed_by_label"] == "관리자(192.168.0.22)"
    client.post(
        "/api/finance/closing/cancel", json=body | {"reason": "잘못 확정"}, headers=_as("관리자")
    )
    cancel = handlers.state().records.closing_cancels[-1]
    assert cancel["confirmed_by_label"] == cancel["canceled_by_label"] == "관리자(192.168.0.22)"


def test_personal_token_keeps_old_shape() -> None:
    client = TestClient(app)
    cleared = client.post(
        "/api/finance/pg/clear",
        json={"reason": "시연 끝"},
        headers=PERSONAL | {"X-Real-IP": "1.2.3.4"},
    ).json()["data"]
    assert cleared["by"] == "taeyoon" and "by_label" not in cleared


def test_cors_allows_actor_header() -> None:
    response = TestClient(app).options(
        "/api/finance/pg/clear",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-ax-actor-name",
        },
    )
    assert response.status_code == 200, response.text
    assert "x-ax-actor-name" in response.headers["access-control-allow-headers"].lower()
