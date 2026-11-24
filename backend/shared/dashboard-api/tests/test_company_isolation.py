"""회사 간 격리 시험 (여러 회사 설계 9장 1~7번, 로드맵 5단계 "격리 시험, CI에 넣기").

시험 회사는 둘이다.
- A `alpha-co`: 새로 들어온 회사
- B `catbooks`: 지금 이 설치의 회사. 설치 기본값(`default_company_id()`·`TENANT_ID` 기본
  `shop-demo`·`AX_TENANT`·회사 연결 파일)이 모두 B를 가리킨다. 실제 운영과 같은 모양이다
  (캣북스 설치에 다른 회사가 들어오는 경우)

계정은 a(A 관리자)·b(B 관리자)·m(A 일반회원)이고,
세션 토큰에 회사(`cid`)·회사 역할(`crole`)을 담는다.
기대는 모두 "없는 것처럼 404"(B 것이 있는지 알리지 않는다)다.
예외는 4번(회사 연결): 화면 주소에 회사가 없고 회사마다 자기 줄을 보여 주므로 "A 자기 연결만 보이고
B 것은 안 보이며 안 바뀐다"를 본다(4번 위 설명).

읽는 법
- 그냥 통과하는 시험: 격리가 이미 되는 곳. 그대로 고정한다
- `xfail(strict=True)`: 지금 새는 곳. 고쳐지면 XPASS로 CI가 빨개지니 그때 표시를 지운다
- `skip`: 이 서버(dashboard-api)에 아직 그 경로가 없다
- `b`가 자기 것을 보는 대조 시험은 준비한 B 자료가 정말 보이는지 확인한다(헛통과 방지)

값은 명백한 더미만 쓴다. 다른 서버는 부르지 않는다(support·마케팅은 대역).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import events, human_auth, llm_usage
from ax_agent_template import tenant as tenants
from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas import AgentRequest, AgentResult, InputReason, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.handoff import HandoffCreate
from ax_dashboard_api import (
    accounts,
    company_ai,
    handoffs,
    marketing_client,
    office_room,
    support_client,
)
from ax_dashboard_api.main import app
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key

A = "alpha-co"
B = "catbooks"
API = "/api/dashboard"
SHOP = "http://10.20.30.40:8080/shop-api"
DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"
SECRET = "MARKETING_VIDEO_API_KEY"
LLM_KEY = "LLM_API_KEY"
A_INQUIRY, B_INQUIRY = 101, 202

STAGE4 = "4단계(세션 회사로 데이터 고르기)"

ROWS = [
    {
        "id": "acc-a", "name": "에이", "email": "a@x.test", "role": "admin",
        "memberships": [{"company_id": A, "role": "admin"}],
        "secret_editor": True, "secret_tenants": [A],
    },
    {
        "id": "acc-b", "name": "비", "email": "b@x.test", "role": "admin",
        "memberships": [{"company_id": B, "role": "admin"}],
        "secret_editor": True, "secret_tenants": [B],
    },
    {
        "id": "acc-m", "name": "엠", "email": "m@x.test", "role": "member",
        "memberships": [{"company_id": A, "role": "member"}],
    },
]  # fmt: skip
WHO = {"a": ("acc-a", A, "admin"), "b": ("acc-b", B, "admin"), "m": ("acc-m", A, "member")}


def _inquiry(inquiry_id: int, subject: str) -> dict[str, Any]:
    return {
        "id": inquiry_id,
        "customer_name": "테스트 고객",
        "subject": subject,
        "status": "open",
        "updated_at": "2026-11-23T00:00:00Z",
        "preview": subject,
        "awaiting_reply": True,
    }


class FakeSupport:
    """support 대역. 회사마다 문의를 따로 두고, 받은 `meta.tenant_id`의 회사 것만 답한다.

    실제 support 서버처럼 `meta.tenant_id`로 회사를 고른다(별칭 `shop-demo`는 B).
    """

    def __init__(self) -> None:
        self.inquiries = {
            A: {A_INQUIRY: _inquiry(A_INQUIRY, "A 회사 문의")},
            B: {B_INQUIRY: _inquiry(B_INQUIRY, "B 회사 문의")},
        }
        self.sent: list[AgentRequest] = []
        self.writes: list[tuple[str, int]] = []

    def _done(self, req: AgentRequest, output: dict[str, Any]) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=output)

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.sent.append(req)
        mine = self.inquiries.get(canonical_company_id(req.meta.tenant_id) or "", {})
        if req.skill == "list_inquiries":
            return self._done(req, {"items": list(mine.values())})
        if req.skill in ("get_inquiry", "send_reply"):
            found = mine.get(req.input["inquiry_id"])
            if found is None:
                return AgentResult(
                    meta=req.meta,
                    status=TaskStatus.FAILED,
                    outcome="not_found",
                    output={"message": "문의를 찾을 수 없다"},
                )
            if req.skill == "send_reply":
                self.writes.append((canonical_company_id(req.meta.tenant_id) or "", found["id"]))
            return self._done(req, {**found, "messages": []})
        if req.skill == "create_inquiry":
            return self._done(req, {**_inquiry(303, "새 문의"), "messages": []})
        return self._done(req, {"items": []})


class FakeMarketing:
    """마케팅 대역: 키 요청 번호를 받아 적용한다. 교체·삭제는 승인 전이라 보류."""

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        svc = resolver.service()
        pending = svc.pending(request.input["request_id"])
        if request.skill == "change_secret":
            svc.record_review(pending.id, "rev-1")
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.INPUT_REQUIRED,
                reason_code=InputReason.HUMAN_INPUT,
                outcome="held",
                output={"review_id": "rev-1"},
            )
        done = svc.apply(pending.id, request.input["updated_by"])
        resolver.invalidate()
        return AgentResult(
            meta=request.meta, status=TaskStatus.COMPLETED, outcome="applied", output=done
        )


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeSupport:
    for name in ("DASHBOARD_MOCK", "AGENT_TEMPLATE_MOCK", "TENANT_ID", "AX_DEFAULT_COMPANY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("AX_COMPANIES_FILE", "AX_COMPANIES_DIR", tenants.TENANTS_DIR_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, B)  # 이 설치의 회사는 B
    for name in (company_ai.DIR_ENV, company_ai.TRIAL_KEY_ENV, company_ai.TRIAL_TOKENS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    for name in (
        human_auth.REVOKED_FILE_ENV,
        human_auth.GENERATIONS_FILE_ENV,
        human_auth.ALLOW_SHARED_ENV,
        human_auth.LEGACY_COMPANY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv(handoffs.DB_ENV, str(tmp_path / "handoffs.db"))
    monkeypatch.setenv(office_room.DB_ENV, str(tmp_path / "office.db"))
    office_room.reset_store()
    created = datetime(2026, 11, 20, tzinfo=UTC).isoformat()
    companies = [
        {"company_id": A, "name": "알파", "created_at": created},
        {"company_id": B, "name": "캣북스", "created_at": created},
    ]
    (tmp_path / "companies.json").write_text(json.dumps({"companies": companies}), encoding="utf-8")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    monkeypatch.setattr(marketing_client, "SEND", FakeMarketing())
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    llm_usage.set_store(llm_usage.SqliteUsageStore(":memory:"))
    yield fake
    llm_usage.reset_store()
    resolver.reset_for_tests()
    office_room.reset_store()


@pytest.fixture
def people(support: FakeSupport) -> dict[str, dict[str, str]]:
    out = {}
    for who, (account_id, company, role) in WHO.items():
        token, _ = human_auth.issue_session(
            account_id=account_id, name=who, role=role, company_id=company, company_role=role
        )
        out[who] = {"Authorization": f"Bearer {token}"}
    return out


@pytest.fixture
def client(support: FakeSupport) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _secret_add(client: TestClient, who: dict, tenant: str, value: str = DUMMY) -> Any:
    return client.post(f"{API}/secrets/{tenant}/{SECRET}/add", json={"value": value}, headers=who)


def _b_history(client: TestClient, people: dict) -> list[dict[str, Any]]:
    data = client.get(f"{API}/secrets", headers=people["b"]).json()["data"]
    return [h for h in data["history"] if h["tenant"] == B]


def _b_handoff() -> str:
    """에이전트가 넣는 B의 할 일 하나(회사 칸 기본값 shop-demo = B)."""
    task = handoffs.get_store().add(
        HandoffCreate(
            tenant_id=TENANT_ID_DEFAULT,
            context_id="ctx_req_b0000001",
            from_service="support",
            to_service="human",
            skill="manual_refund",
            reason="B 회사 확인 필요",
            assignee_unit="support",
        )
    )
    return task.task_id


# ---- 1. a 토큰으로 B 문의 번호 조회·답변 쓰기 → 404 ----


def test_1_control_b_reads_its_own_inquiry(client: TestClient, people: Any) -> None:
    res = client.get(f"{API}/conversations/{B_INQUIRY}", headers=people["b"])
    assert res.status_code == 200 and res.json()["data"]["subject"] == "B 회사 문의"


def test_1_a_cannot_read_or_answer_b_inquiry(
    client: TestClient, people: Any, support: FakeSupport
) -> None:
    assert client.get(f"{API}/conversations/{B_INQUIRY}", headers=people["a"]).status_code == 404
    reply = client.post(
        f"{API}/conversations/{B_INQUIRY}/agent-messages",
        json={"text": "안내"},
        headers=people["a"],
    )
    assert reply.status_code == 404
    assert support.writes == []


# ---- 2. a 토큰으로 B 보고서(일간 리포트·트렌드 보고서·분석 의뢰 PDF) 조회 → 404 ----


@pytest.mark.skip(
    reason=(
        "dashboard-api에 보고서 경로가 없다. 화면이 data A2A(/a2a)를 공용 에이전트 토큰으로 바로 "
        "부르고 사람 세션이 없다. data의 trend_report_get·list도 회사 칸 없이 report_id로만 찾는다 "
        f"— 보고서를 세션 경로로 옮길 때(데이터 담당, {STAGE4}) 여기 시험을 채운다"
    )
)
def test_2_a_cannot_read_b_reports() -> None:
    raise AssertionError("경로가 생기면 채운다")


# ---- 3. a 토큰으로 B 키 목록·키 값·키 바꾸기 → 404, B 키 이력에 줄 없음 ----


def test_3_b_keys_are_not_listed_and_change_requests_leave_no_trace(
    client: TestClient, people: Any
) -> None:
    assert _secret_add(client, people["b"], B).status_code == 200
    before = _b_history(client, people)
    assert before  # 대조: B 키가 정말 있고 이력도 있다

    mine = client.get(f"{API}/secrets", headers=people["a"])
    assert mine.status_code == 200
    data = mine.json()["data"]
    assert [t["tenant"] for t in data["tenants"]] == [A]
    assert all(r["tenant"] != B for r in data["requests"] + data["history"])
    assert DUMMY not in mine.text and DUMMY[-4:] not in mine.text  # 값·끝 글자도 없다

    tried = [
        client.post(
            f"{API}/secrets/{B}/{SECRET}/replace", json={"value": DUMMY2}, headers=people["a"]
        ),
        client.post(f"{API}/secrets/{B}/{SECRET}/delete", json={}, headers=people["a"]),
        _secret_add(client, people["a"], B, DUMMY2),
    ]
    for res in tried:
        assert res.status_code in (403, 404), res.text  # 막힌다 (404로 바꾸기는 아래 xfail)
        assert DUMMY[-4:] not in res.text
    assert _b_history(client, people) == before  # B 이력에 줄이 늘지 않는다
    pending = client.get(f"{API}/secrets", headers=people["b"]).json()["data"]["requests"]
    assert pending == []


def test_3_b_key_change_answers_404(client: TestClient, people: Any) -> None:
    _secret_add(client, people["b"], B)
    res = client.post(
        f"{API}/secrets/{B}/{SECRET}/replace", json={"value": DUMMY2}, headers=people["a"]
    )
    assert res.status_code == 404


def test_3_secret_grant_is_bound_to_the_session_company(
    client: TestClient, people: Any, tmp_path: Path
) -> None:
    _secret_add(client, people["b"], B)
    rows = [dict(r, secret_tenants=[A, B]) if r["id"] == "acc-a" else r for r in ROWS]
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    data = client.get(f"{API}/secrets", headers=people["a"]).json()["data"]
    assert [t["tenant"] for t in data["tenants"]] == [A]


def test_3_a_cannot_see_or_change_b_company_ai_key(client: TestClient, people: Any) -> None:
    """회사 AI 설정은 로그인 세션 회사 것만 보고 바꾼다(11/24). A는 B 키를 보지도 바꾸지도 못한다.

    예전 기대(A의 GET·키 저장이 404)는 회사별 설정이 없을 때의 막기였다.
    이제 A는 A 회사 설정(빈 값)을 보고 A 키만 넣는다. 격리는 더 세게 본다:
    A 화면에 B 회사·B 키가 없고, A가 넣어도 B 키는 그대로다."""
    put = client.post(
        f"{API}/company-ai/keys/{LLM_KEY}", json={"value": DUMMY}, headers=people["b"]
    )
    assert put.status_code == 200, put.text  # 대조: B 관리자는 B 키를 넣는다

    def view(who: str) -> dict:
        res = client.get(f"{API}/company-ai", headers=people[who])
        assert res.status_code == 200, res.text
        return res.json()["data"]

    def llm(data: dict) -> dict:
        return next(k for k in data["keys"] if k["name"] == LLM_KEY)

    b_before = llm(view("b"))
    seen = view("a")
    assert seen["company"]["id"] == A
    assert llm(seen)["has_key"] is False and llm(seen)["hint"] == ""  # B 키가 안 보인다
    client.post(f"{API}/company-ai/keys/{LLM_KEY}", json={"value": DUMMY2}, headers=people["a"])
    assert llm(view("b")) == b_before  # A가 넣어도 B 키는 그대로
    assert view("a")["company"]["id"] == A


# ---- 4. a 토큰으로 B 회사 연결·변환기 매핑 읽기·쓰기 → B 것은 안 보이고 안 바뀐다 ----
# 회사 연결이 회사마다 한 줄이 된 뒤(여러 회사 설계 6.2·6.3, PR #1634)의 기대값.
# 화면 주소(`/company-connection`·`/site-adapter`)에 회사가 없고 세션 회사의 줄을 보여 주므로,
# A 관리자가 받는 것은 404가 아니라 "A 자기 연결(아직 없음)"이다.
# B 연결의 주소·짝·키·이력·회사 설정은 A 응답에 하나도 없고,
# A의 모드 바꾸기·해제는 A 줄에만 닿는다(B 연결은 켜진 그대로).
# 예전 xfail(404 기대)보다 약하지 않다: 읽기 격리(B 값 없음)·쓰기 격리(B 그대로)를 둘 다 보고,
# B 관리자가 자기 연결을 보는 대조로 헛통과를 막는다. (기대값 변경은 태윤 님 판단 필요)


def test_4_a_sees_only_its_own_connection_and_cannot_change_b(
    client: TestClient, people: Any
) -> None:
    cc.save_connection(SHOP, verified_at=None, updated_by="비", tenant=B, company_id=B)
    # 대조: B 관리자는 B 연결을 본다
    mine = client.get(f"{API}/company-connection", headers=people["b"]).json()["data"]
    assert mine["connection"]["shop_api_url"] == SHOP and mine["tenant"]["id"] == B
    for path in ("company-connection", "site-adapter"):
        res = client.get(f"{API}/{path}", headers=people["a"])
        assert res.status_code == 200, res.text
        assert "10.20.30.40" not in res.text  # B 연결 주소가 없다
        assert res.json()["data"]["connection"] is None  # A 자기 연결(아직 없음)
    state = client.get(f"{API}/company-connection", headers=people["a"]).json()["data"]
    assert state["tenant"] is None and state["history"] == []  # B 회사 설정·B 이력도 없다
    assert state["connected"] is False
    res = client.post(
        f"{API}/company-connection/mode", json={"mode": "internal"}, headers=people["a"]
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_CONNECTION"
    assert client.post(f"{API}/company-connection/clear", headers=people["a"]).status_code == 200
    found = cc.read_connection(B)
    assert found is not None and found.enabled and found.shop_api_url == SHOP


# ---- 5. a 토큰으로 B 할 일함·승인 대기·AI 오피스 대화 → 목록 0건, 단건 404 ----


def test_5_control_b_sees_its_own_handoff(client: TestClient, people: Any) -> None:
    task_id = _b_handoff()
    rows = client.get(f"{API}/handoffs", headers=people["b"]).json()["data"]
    assert [t["task_id"] for t in rows] == [task_id]


def test_5_a_sees_no_b_handoff_and_cannot_close_one(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[Any] = []

    async def publish(envelope: Any) -> None:
        published.append(envelope)

    monkeypatch.setattr(events, "publish", publish)
    task_id = _b_handoff()
    assert client.get(f"{API}/handoffs", headers=people["a"]).json()["data"] == []
    res = client.post(
        f"{API}/handoffs/{task_id}/complete",
        json={"status": "done", "result": "처리"},
        headers=people["a"],
    )
    assert res.status_code == 404
    assert published == []


def test_5_a_sees_no_b_office_thread(client: TestClient, people: Any) -> None:
    """오피스 대화 목록은 회사 칸(tenant_id)으로 나뉜다(11/24). B 회사 줄은 A에게 안 보인다."""
    body = office_room.OfficeThreadBody(
        agent="dev", thread_id="th_b_only", from_id="acc-b", from_name="비"
    )
    office_room.get_store().touch(body, datetime.now(UTC), owner_who="비", tenant=B)
    rows = client.get(f"{API}/office-room", headers=people["a"]).json()["data"]
    assert all(r["thread_id"] != "th_b_only" for r in rows)


@pytest.mark.skip(
    reason=(
        "dashboard-api에 승인 대기 경로가 없다(/reviews는 목 전용). 화면이 안전 관문 "
        "/api/safety/reviews를 바로 읽고, 안전 관문 목록은 회사로 거르지 않는다 — "
        f"안전 관문 담당 {STAGE4}에서 이 시험을 채운다"
    )
)
def test_5_a_sees_no_b_pending_approval() -> None:
    raise AssertionError("경로가 생기면 채운다")


# ---- 6. m(A 일반회원) 토큰으로 B 데이터 → 404 (회사 바꾸기 길은 없다) ----


def test_6_member_cannot_reach_admin_screens_or_switch_the_company(
    client: TestClient, people: Any
) -> None:
    """일반회원은 키·회사 AI·회사 연결·변환기를 못 쓴다(역할로 막혀 B가 있는지와 무관한 403).

    회사 연결을 바꾸거나 끊는 길(저장·모드·해제)도 일반회원에게 없다.
    """
    client.post(f"{API}/company-ai/keys/{LLM_KEY}", json={"value": DUMMY}, headers=people["b"])
    cc.save_connection(SHOP, verified_at=None, updated_by="비", tenant=B)
    m = people["m"]
    tried = [
        client.get(f"{API}/secrets", headers=m),
        _secret_add(client, m, B),
        client.get(f"{API}/company-ai", headers=m),
        client.post(f"{API}/company-ai/keys/{LLM_KEY}", json={"value": DUMMY2}, headers=m),
        client.put(f"{API}/company-connection", json={"url": SHOP, "tenant": A}, headers=m),
        client.post(f"{API}/company-connection/save", json={"url": SHOP, "tenant": A}, headers=m),
        client.post(f"{API}/company-connection/test", json={"url": SHOP}, headers=m),
        client.post(f"{API}/company-connection/mode", json={"mode": "internal"}, headers=m),
        client.post(f"{API}/company-connection/clear", headers=m),
        client.delete(f"{API}/company-connection", headers=m),
        client.post(f"{API}/site-adapter/propose", json={"base_url": SHOP}, headers=m),
        client.put(f"{API}/site-adapter", json={}, headers=m),
        client.post(f"{API}/site-adapter/save", json={}, headers=m),
        # 회사 연결 › 사이트 쓰기 (W4)
        client.post(f"{API}/site-adapter/writes/map", json={"action": "refund"}, headers=m),
        client.post(f"{API}/site-adapter/inboxes/map", json={"inbox": "claims"}, headers=m),
        client.post(
            f"{API}/site-adapter/writes/switch",
            json={"action": "refund", "on": True, "confirm_money": True},
            headers=m,
        ),
        client.post(
            f"{API}/site-adapter/writes/denied/clear", json={"action": "refund"}, headers=m
        ),
    ]
    for res in tried:
        assert res.status_code in (403, 404), (res.request.method, res.request.url, res.text)
        assert DUMMY[-4:] not in res.text
    found = cc.read_connection()
    assert found is not None and found.enabled and found.tenant == B  # 그대로다


def test_6_member_cannot_read_b_inquiry_or_handoff(client: TestClient, people: Any) -> None:
    _b_handoff()
    assert client.get(f"{API}/conversations/{B_INQUIRY}", headers=people["m"]).status_code == 404
    assert client.get(f"{API}/handoffs", headers=people["m"]).json()["data"] == []


# ---- 7. 본문·쿼리에 tenant_id=B를 넣은 a의 요청 → 무시하고 A로 처리 ----


def test_7_tenant_id_in_query_or_body_does_not_pick_the_company(
    client: TestClient, people: Any, support: FakeSupport
) -> None:
    """요청 값의 회사는 무시한다: 넣든 안 넣든 support로 가는 회사·결과가 같다."""
    a = people["a"]
    plain = client.get(f"{API}/conversations", headers=a)
    forced = client.get(f"{API}/conversations", params={"tenant_id": B}, headers=a)
    assert plain.status_code == forced.status_code == 200
    assert plain.json()["data"] == forced.json()["data"]
    body = {"user_id": 1, "subject": "제목", "message": "본문"}
    client.post(f"{API}/conversations", json=body, headers=a)
    client.post(f"{API}/conversations", json={**body, "tenant_id": A}, headers=a)
    tenants_sent = {req.meta.tenant_id for req in support.sent}
    assert len(tenants_sent) == 1  # 요청 값으로 회사가 바뀌지 않는다
    assert all("tenant_id" not in req.input for req in support.sent)

    _b_handoff()
    plain_rows = client.get(f"{API}/handoffs", headers=a).json()["data"]
    forced_rows = client.get(f"{API}/handoffs", params={"tenant_id": A}, headers=a).json()["data"]
    assert plain_rows == forced_rows


def test_7_a_request_is_processed_as_company_a(
    client: TestClient, people: Any, support: FakeSupport
) -> None:
    res = client.get(f"{API}/conversations", params={"tenant_id": B}, headers=people["a"])
    assert [c["id"] for c in res.json()["data"]] == [A_INQUIRY]
    assert {req.meta.tenant_id for req in support.sent} == {A}
