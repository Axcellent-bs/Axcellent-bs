"""회사 연결 › 사이트 쓰기 화면 (③-쓰기 W4).

- 주문·상품 짝 저장이 쓰기 짝(`writes`)·받은함 짝(`inboxes`)을 지우지 않는다
- 쓰기 짝·받은함 짝 저장(모양 검사), 동작별 직접 쓰기 켜기·끄기(관리자만, 돈이 걸린 동작은 확인)
- "키에 쓰기 권한 없음" 풀기, 최근 사이트 쓰기(키·본문 원문 없음)
- 모두 로그인 세션의 회사 줄만 (다른 회사 줄은 그대로)
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies, company_files, company_scope, human_auth
from ax_agent_template import company_connection as cc
from ax_agent_template import tenant as tenants
from ax_dashboard_api import accounts
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import direct, writes
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

FIXTURES = Path(__file__).parent / "fixtures"
CAT_SITE = "http://10.0.0.8:3000"
NUBI_SITE = "http://10.0.0.9:3000"
CAT_KEY = "cat-site-key"
NUBI_KEY = "nubi-site-key"
SCREEN = "/api/dashboard/site-adapter"
ROWS = [
    {"id": "acc-cat", "name": "캣", "email": "c@x.test", "role": "admin",
     "memberships": [{"company_id": "catbooks", "role": "admin"}]},
    {"id": "acc-nubi", "name": "누비", "email": "n@x.test", "role": "admin",
     "memberships": [{"company_id": "nubi", "role": "admin"}]},
    {"id": "acc-nubi-m", "name": "누비회원", "email": "nm@x.test", "role": "member",
     "memberships": [{"company_id": "nubi", "role": "member"}]},
]  # fmt: skip
OPENAPI = {
    "paths": {
        "/api/v1/bookings": {"get": {}},
        "/api/v1/tours": {"get": {}},
        "/api/v1/cancellations": {"get": {}},
        "/api/v1/cancellations/{id}/decision": {"post": {}},
        "/api/v1/inquiries/{id}/answer": {"post": {}, "get": {}},
        "/api/v1/departures/{id}/status": {"put": {}, "patch": {}},
        "//evil.test/x": {"post": {}},
        "/api/v1/odd?x=1": {"post": {}},
    }
}


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / ".data"
    data.mkdir()
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(data / "company_connection.json"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(data / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    monkeypatch.delenv(m.MAPPING_DIR_ENV, raising=False)
    monkeypatch.setattr(m, "DEFAULT_DIR", data / "site_adapter")
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.delenv(tenants.TENANT_ENV, raising=False)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.delenv(up.TOKEN_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    company_files.reset_for_tests()
    return data


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    got: list[httpx.Request] = []
    pages = {
        "/openapi.json": OPENAPI,
        "/api/v1/tours": load("nubi_tours.json"),
        "/api/v1/bookings": load("nubi_bookings_p1.json"),
        "/api/v1/cancellations": load("nubi_cancellations_p1.json"),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        got.append(request)
        body = pages.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    return got


@pytest.fixture
def people(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """캣북스 관리자·누비 관리자·누비 일반회원 로그인 세션 머리글."""
    for name in (accounts.ACCOUNTS_FILE_ENV, "AX_COMPANIES_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name in (
        human_auth.REVOKED_FILE_ENV,
        human_auth.GENERATIONS_FILE_ENV,
        human_auth.ALLOW_SHARED_ENV,
        human_auth.LEGACY_COMPANY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    created = datetime(2026, 11, 23, tzinfo=UTC).isoformat()
    listed = [
        {"company_id": cid, "name": cid, "created_at": created} for cid in ("catbooks", "nubi")
    ]
    (root / "companies.json").write_text(json.dumps({"companies": listed}), encoding="utf-8")
    (root / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    human_auth.reset_revoked_for_tests()
    out = {}
    for who, (account, company, role) in {
        "cat": ("acc-cat", "catbooks", "admin"),
        "nubi": ("acc-nubi", "nubi", "admin"),
        "member": ("acc-nubi-m", "nubi", "member"),
    }.items():
        token, _ = human_auth.issue_session(
            account_id=account, name=who, role=role, company_id=company, company_role=role
        )
        out[who] = {"Authorization": f"Bearer {token}"}
    return out


@pytest.fixture
def client(root: Path, seen: list[httpx.Request]) -> Iterator[TestClient]:
    yield TestClient(app)


def _connect(company: str, site: str, key: str, *, extras: bool = False) -> Path:
    """그 회사 줄에 누비 짝(원하면 쓰기·받은함 짝까지)·키·kind=adapter 연결을 깐다."""
    folder = m.adapter_dir(company)
    folder.mkdir(parents=True, exist_ok=True)
    raw = load("nubi_mapping.json")
    if extras:
        raw["writes"] = load("nubi_writes.json")
        raw["inboxes"] = load("nubi_inboxes.json")
    (folder / m.MAPPING_FILE).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    (folder / up.TOKEN_FILE).write_text(key, encoding="utf-8")
    cc.save_connection(
        site,
        verified_at=None,
        updated_by="관리자",
        kind="adapter",
        adapter={"base_url": site},
        company_id=company,
    )
    return folder


def _saved(folder: Path) -> dict[str, Any]:
    return json.loads((folder / m.MAPPING_FILE).read_text(encoding="utf-8"))


def _screen_mapping() -> dict[str, Any]:
    """회사 연결 화면이 보내는 짝 (주문·상품만)."""
    raw = load("nubi_mapping.json")
    return {k: raw[k] for k in ("version", "auth_header", "auth_scheme", "resources")}


def _action(state: dict[str, Any], name: str) -> dict[str, Any]:
    return next(a for a in state["actions"] if a["action"] == name)


# ---- 1. 저장이 덮어쓰지 않음 ----


def test_resource_save_keeps_writes_and_inboxes(client: TestClient, people: Any) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    body = {"base_url": NUBI_SITE, "mapping": _screen_mapping()}
    res = client.post(SCREEN + "/save", json=body, headers=people["nubi"])
    assert res.status_code == 200, res.text
    saved = _saved(folder)
    expected = m.SiteMapping.model_validate(
        {**_screen_mapping(), "writes": load("nubi_writes.json")}
    ).model_dump()
    assert saved["writes"] == expected["writes"]
    assert saved["inboxes"]["claims"]["path"] == "/api/v1/cancellations"
    # PUT도 같다
    assert client.put(SCREEN, json=body, headers=people["nubi"]).status_code == 200
    assert set(_saved(folder)["writes"]) == {"inquiry_answer", "refund"}


def test_resource_save_changes_only_what_was_sent(client: TestClient, people: Any) -> None:
    """보낸 칸은 바꾼다: `writes: {}`를 보내면 쓰기 짝을 비우고, 받은함 짝은 그대로."""
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    body = {"base_url": NUBI_SITE, "mapping": {**_screen_mapping(), "writes": {}}}
    assert client.post(SCREEN + "/save", json=body, headers=people["nubi"]).status_code == 200
    saved = _saved(folder)
    assert saved["writes"] == {}
    assert "claims" in saved["inboxes"]


def test_resource_save_without_saved_mapping_still_works(client: TestClient, people: Any) -> None:
    body = {"base_url": NUBI_SITE, "token": NUBI_KEY, "mapping": _screen_mapping()}
    res = client.post(SCREEN + "/save", json=body, headers=people["nubi"])
    assert res.status_code == 200, res.text
    saved = _saved(m.adapter_dir("nubi"))
    assert saved["writes"] == {} and saved["inboxes"] == {}


# ---- 2. 쓰기 경로 후보·쓰기 짝 ----


def test_propose_lists_write_paths_and_claims_sample(client: TestClient, people: Any) -> None:
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    body = {"base_url": NUBI_SITE, "claims_path": "/api/v1/cancellations"}
    res = client.post(SCREEN + "/propose", json=body, headers=people["nubi"])
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["write_paths"] == [
        {"method": "POST", "path": "/api/v1/cancellations/{id}/decision"},
        {"method": "PUT", "path": "/api/v1/departures/{id}/status"},
        {"method": "PATCH", "path": "/api/v1/departures/{id}/status"},
        {"method": "POST", "path": "/api/v1/inquiries/{id}/answer"},
    ]  # 다른 호스트로 새는 경로(`//`·`?`)는 빼고
    sample = data["claims_sample"]
    assert sample["count"] == 2 and "booking_code" in sample["keys"]
    assert NUBI_KEY not in res.text


def test_write_map_saves_one_action_and_keeps_the_rest(client: TestClient, people: Any) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY)
    before = _saved(folder)
    refund = load("nubi_writes.json")["refund"]
    res = client.post(
        SCREEN + "/writes/map", json={"action": "refund", "write": refund}, headers=people["nubi"]
    )
    assert res.status_code == 200, res.text
    state = res.json()["data"]
    assert _action(state, "refund")["write"]["path"] == "/api/v1/cancellations/{site_id}/decision"
    assert _action(state, "refund")["money"] is True
    assert _action(state, "refund")["enabled"] is False  # 저장해도 켜지지 않는다(기본 꺼짐)
    assert [a["action"] for a in state["actions"]][:2] == ["inquiry_answer", "refund"]
    saved = _saved(folder)
    assert saved["resources"] == m.SiteMapping.model_validate(before).model_dump()["resources"]
    assert set(saved["writes"]) == {"refund"}
    # 두 번째 동작을 더해도 첫 동작은 그대로
    answer = load("nubi_writes.json")["inquiry_answer"]
    client.post(
        SCREEN + "/writes/map",
        json={"action": "inquiry_answer", "write": answer},
        headers=people["nubi"],
    )
    assert set(_saved(folder)["writes"]) == {"refund", "inquiry_answer"}
    # 지우기
    res = client.post(
        SCREEN + "/writes/map", json={"action": "refund", "write": None}, headers=people["nubi"]
    )
    assert res.status_code == 200
    assert set(_saved(folder)["writes"]) == {"inquiry_answer"}


@pytest.mark.parametrize(
    "broken",
    [
        {"method": "POST", "path": "/a/{site_id}", "path_params": {}},  # 경로 칸 짝 없음
        {"method": "POST", "path": "//evil.test/a"},  # 다른 호스트
        {"method": "GET", "path": "/a"},  # 쓰기 메서드 아님
        {"method": "POST", "path": "/a", "ok": [500]},  # 2xx 아님
        {"method": "POST", "path": "/a", "body": {"x": "now_plus:abc"}},  # 식 모양
        {"method": "POST", "path": "/a", "extra": 1},  # 모르는 칸
    ],
)
def test_write_map_checks_the_shape(
    client: TestClient, people: Any, broken: dict[str, Any]
) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY)
    before = (folder / m.MAPPING_FILE).read_text(encoding="utf-8")
    res = client.post(
        SCREEN + "/writes/map",
        json={"action": "inquiry_answer", "write": broken},
        headers=people["nubi"],
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "MAPPING_BROKEN"
    assert (folder / m.MAPPING_FILE).read_text(encoding="utf-8") == before


def test_write_map_needs_a_connected_mapping(client: TestClient, people: Any) -> None:
    res = client.post(
        SCREEN + "/writes/map",
        json={"action": "refund", "write": load("nubi_writes.json")["refund"]},
        headers=people["nubi"],
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_CONFIGURED"


# ---- 받은함 짝 ----


def test_inbox_map_reads_the_first_page_then_saves(
    client: TestClient, people: Any, seen: list[httpx.Request]
) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY)
    claims = load("nubi_inboxes.json")["claims"]
    seen.clear()
    res = client.post(
        SCREEN + "/inboxes/map", json={"inbox": "claims", "map": claims}, headers=people["nubi"]
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["count"] == 2
    assert res.json()["data"]["inboxes"]["claims"]["path"] == "/api/v1/cancellations"
    assert [r.url.path for r in seen] == ["/api/v1/cancellations"]
    assert seen[0].headers["authorization"] == f"Bearer {NUBI_KEY}"  # 저장된 그 회사 키
    assert _saved(folder)["inboxes"]["claims"]["fields"]["claim_id"] == "id"
    # 지우기
    res = client.post(
        SCREEN + "/inboxes/map", json={"inbox": "claims", "map": None}, headers=people["nubi"]
    )
    assert res.status_code == 200 and _saved(folder)["inboxes"] == {}


def test_inbox_map_needs_required_fields_and_a_working_mapping(
    client: TestClient, people: Any
) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY)
    claims = load("nubi_inboxes.json")["claims"]
    missing = {**claims, "fields": {k: v for k, v in claims["fields"].items() if k != "claim_id"}}
    res = client.post(
        SCREEN + "/inboxes/map", json={"inbox": "claims", "map": missing}, headers=people["nubi"]
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_INCOMPLETE"
    wrong = {**claims, "fields": {**claims["fields"], "order_id": "no_such_field"}}
    res = client.post(
        SCREEN + "/inboxes/map", json={"inbox": "claims", "map": wrong}, headers=people["nubi"]
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_BROKEN"
    assert _saved(folder).get("inboxes", {}) == {}


# ---- 3. 동작별 직접 쓰기 켜기 ----


def _switch(client: TestClient, headers: Any, action: str, on: bool, **extra: Any) -> Any:
    body = {"action": action, "on": on, **extra}
    return client.post(SCREEN + "/writes/switch", json=body, headers=headers)


def test_switch_on_and_off_per_action(client: TestClient, people: Any) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    res = _switch(client, people["nubi"], "inquiry_answer", True)
    assert res.status_code == 200, res.text
    assert _action(res.json()["data"], "inquiry_answer")["enabled"] is True
    with company_scope.using("nubi"):
        assert writes.direct_write_on("inquiry_answer") is True
        assert writes.direct_write_on("refund", "exec_1") is False  # 다른 동작은 그대로 꺼짐
    raw = json.loads((folder / writes.SWITCH_FILE).read_text(encoding="utf-8"))
    assert raw["enabled"] == ["inquiry_answer"]
    assert raw["changes"][-1]["action"] == "inquiry_answer" and raw["changes"][-1]["on"] is True
    res = _switch(client, people["nubi"], "inquiry_answer", False)
    assert res.status_code == 200
    with company_scope.using("nubi"):
        assert writes.direct_write_on("inquiry_answer") is False


def test_money_action_needs_the_confirm(client: TestClient, people: Any) -> None:
    _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    res = _switch(client, people["nubi"], "refund", True)
    assert res.status_code == 422 and res.json()["error"]["code"] == "MONEY_CONFIRM_REQUIRED"
    with company_scope.using("nubi"):
        assert writes.enabled_actions() == []
    res = _switch(client, people["nubi"], "refund", True, confirm_money=True)
    assert res.status_code == 200, res.text
    with company_scope.using("nubi"):
        assert writes.direct_write_on("refund", "exec_1") is True
        assert writes.direct_write_on("refund") is False  # 관문을 안 지난 쓰기는 여전히 꺼짐
    # 끄기는 확인 없이 된다
    assert _switch(client, people["nubi"], "refund", False).status_code == 200


def test_switch_needs_a_write_map(client: TestClient, people: Any) -> None:
    _connect("nubi", NUBI_SITE, NUBI_KEY)  # 쓰기 짝 없음
    res = _switch(client, people["nubi"], "inquiry_answer", True)
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_WRITE_MAP"


def test_member_cannot_change_writes(client: TestClient, people: Any) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    before = (folder / m.MAPPING_FILE).read_text(encoding="utf-8")
    member = people["member"]
    tried = [
        _switch(client, member, "inquiry_answer", True),
        _switch(client, member, "refund", True, confirm_money=True),
        client.post(
            SCREEN + "/writes/map", json={"action": "refund", "write": None}, headers=member
        ),
        client.post(SCREEN + "/inboxes/map", json={"inbox": "claims", "map": None}, headers=member),
        client.post(SCREEN + "/writes/denied/clear", json={"action": "refund"}, headers=member),
    ]
    for res in tried:
        assert res.status_code == 403, (res.request.url, res.text)
        assert res.json()["error"]["code"] == "ROLE_REQUIRED"
    assert not (folder / writes.SWITCH_FILE).exists()
    assert (folder / m.MAPPING_FILE).read_text(encoding="utf-8") == before
    # 읽기는 된다
    assert client.get(SCREEN + "/writes", headers=member).status_code == 200


def test_switch_does_not_touch_the_other_company(client: TestClient, people: Any) -> None:
    cat = _connect("catbooks", CAT_SITE, CAT_KEY, extras=True)
    _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    (cat / writes.SWITCH_FILE).write_text(json.dumps({"enabled": ["refund"]}), encoding="utf-8")
    cat_before = (cat / writes.SWITCH_FILE).read_text(encoding="utf-8")
    assert _switch(client, people["nubi"], "inquiry_answer", True).status_code == 200
    assert _switch(client, people["nubi"], "refund", False).status_code == 200
    assert (cat / writes.SWITCH_FILE).read_text(encoding="utf-8") == cat_before
    cat_state = client.get(SCREEN + "/writes", headers=people["cat"]).json()["data"]
    nubi_state = client.get(SCREEN + "/writes", headers=people["nubi"]).json()["data"]
    assert _action(cat_state, "refund")["enabled"] is True
    assert _action(cat_state, "inquiry_answer")["enabled"] is False
    assert _action(nubi_state, "inquiry_answer")["enabled"] is True
    assert _action(nubi_state, "refund")["enabled"] is False


# ---- 4. 키에 쓰기 권한 없음 ----


def test_denied_mark_shows_and_clears(client: TestClient, people: Any) -> None:
    cat = _connect("catbooks", CAT_SITE, CAT_KEY, extras=True)
    nubi = _connect("nubi", NUBI_SITE, NUBI_KEY, extras=True)
    with company_scope.using("nubi"):
        direct.mark_denied("refund")
        direct.mark_denied("inquiry_answer")
    with company_scope.using("catbooks"):
        direct.mark_denied("refund")
    state = client.get(SCREEN + "/writes", headers=people["nubi"]).json()["data"]
    assert _action(state, "refund")["denied_at"]
    res = client.post(
        SCREEN + "/writes/denied/clear", json={"action": "refund"}, headers=people["nubi"]
    )
    assert res.status_code == 200, res.text
    state = res.json()["data"]
    assert _action(state, "refund")["denied_at"] is None
    assert _action(state, "inquiry_answer")["denied_at"]  # 다른 동작 표시는 그대로
    with company_scope.using("nubi"):
        assert set(direct.denied_actions()) == {"inquiry_answer"}
        assert "refund" not in direct.denied_actions()
    raw = json.loads((cat / direct.DENIED_FILE).read_text(encoding="utf-8"))
    assert set(raw["actions"]) == {"refund"}  # 캣북스 표시는 그대로
    assert (nubi / direct.DENIED_FILE).exists()


# ---- 5. 최근 사이트 쓰기 ----

SECRET_BODY = "고객 문의에 대한 답변 원문"
SECRET_KEY = "idem-key-raw-value"


def _ledger_row(at: datetime, status: str, applied: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "at": at.isoformat(timespec="seconds"),
        "service": "support",
        "action": "inquiry_answer",
        "shop_path": "/api/shop/admin/inquiries/7/answer",
        "context_id": "ctx_secret_ctx",
        "execution_id": "exec_secret",
        "key_digest": writes.digest(SECRET_KEY),
        "fingerprint": writes.digest({"answer": SECRET_BODY}),
        "status": status,
        "reason": "site_unknown" if status == "unknown" else None,
        "site_method": "POST",
        "site_host": "10.0.0.9",
        "site_path": "/api/v1/inquiries/{site_id}/answer",
        "site_status": 200 if status == "done" else 504,
        "site_request_id": "req-1",
        "site_code": None,
        "handoff_task_id": None if status == "done" else "task_1",
    }
    if applied:
        row["response"] = {"seq": 7, "answer": SECRET_BODY, "applied": applied}
    return row


def test_recent_writes_show_no_key_or_body(client: TestClient, people: Any) -> None:
    folder = _connect("nubi", NUBI_SITE, NUBI_KEY)
    start = datetime(2026, 11, 23, tzinfo=UTC)
    ledger = {
        f"support|inquiry_answer|{i}": _ledger_row(
            start + timedelta(minutes=i),
            "done" if i % 2 else "unknown",
            "site" if i % 2 else "pending_human",
        )
        for i in range(60)
    }
    (folder / writes.LEDGER_FILE).write_text(json.dumps(ledger), encoding="utf-8")
    res = client.get(SCREEN + "/writes/recent", headers=people["nubi"])
    assert res.status_code == 200, res.text
    items = res.json()["data"]["items"]
    assert len(items) == 50
    assert items[0]["at"] > items[-1]["at"]  # 새것 먼저
    assert items[0]["status"] == "done" and items[0]["applied"] == "site"
    assert items[1]["status"] == "unknown" and items[1]["applied"] == "pending_human"
    assert items[1]["reason_text"] == writes.REASONS["site_unknown"]
    assert items[0]["site_path"] == "/api/v1/inquiries/{site_id}/answer"
    assert items[0]["site_status"] == 200
    for leaked in (
        SECRET_BODY,
        SECRET_KEY,
        writes.digest(SECRET_KEY),
        writes.digest({"answer": SECRET_BODY}),
        "ctx_secret_ctx",
        "exec_secret",
        NUBI_KEY,
    ):
        assert leaked not in res.text
    assert set(items[0]) == {*writes.RECENT_FIELDS, "applied", "reason_text"}


def test_recent_writes_are_per_company(client: TestClient, people: Any) -> None:
    cat = _connect("catbooks", CAT_SITE, CAT_KEY)
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    row = _ledger_row(datetime(2026, 11, 23, tzinfo=UTC), "done", "site")
    (cat / writes.LEDGER_FILE).write_text(json.dumps({"k": row}), encoding="utf-8")
    nubi_items = client.get(SCREEN + "/writes/recent", headers=people["nubi"]).json()["data"]
    cat_items = client.get(SCREEN + "/writes/recent", headers=people["cat"]).json()["data"]
    assert nubi_items["items"] == []
    assert len(cat_items["items"]) == 1


def test_writes_state_without_mapping_is_empty(client: TestClient, people: Any) -> None:
    res = client.get(SCREEN + "/writes", headers=people["nubi"])
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["configured"] is False
    assert all(a["write"] is None and a["enabled"] is False for a in data["actions"])
    assert data["inboxes"] == {"claims": None}
    recent = client.get(SCREEN + "/writes/recent", headers=people["nubi"]).json()["data"]
    assert recent["items"] == []
