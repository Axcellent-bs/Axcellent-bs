"""사이트 변환기 W5: 누비투어 취소 신청 읽기(`GET /admin/claims`) + 환불 = 취소 결정 쓰기.

설계 "③-쓰기" W5, "① 짝 맞춰 읽기" 신청 받은함. 고객사는 `upstream.TRANSPORT`, safety는
`gate_check.TRANSPORT`(httpx.MockTransport)로 흉내 낸다.

누비투어 근거 (`Catbooks-demo/nubi-tour` dev `c38542c`):
- `GET /api/v1/cancellations` (`cancellationV1`, 커서 쪽 넘김)
- `POST /api/v1/cancellations/{id}/decision`
  `{decision: approve|reject, refund_amount?: 정수 0 이상, reason?: 500자까지}`
  → 200 `{id, booking_code, status, fee, refund_amount, decided_at, replayed}`,
  400 `REASON_REQUIRED`·`BAD_REFUND`, 404 `NOT_FOUND`, 409 `ALREADY_DECIDED`·`BAD_STATE`,
  502 `REFUND_FAILED`(결정은 반영, 결제사 환불만 실패)
"""

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_contracts.schemas import TENANT_ID_DEFAULT
from ax_dashboard_api import handoffs
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import claims, direct, gate_check, links, writes
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
BASE = "/site-adapter"
SUPPORT = {"authorization": "Bearer sup-tok"}
SITE_TOKEN = "site-secret-token-9"
FIXTURES = Path(__file__).parent / "fixtures"
CLAIMS_PATH = f"{BASE}/api/shop/admin/claims"
# 신청 cl_01(예약 NB-20261110-0001)은 처음 읽을 때 두 번째로 보여 번호 2를 받는다
ORDER = "NB-20261110-0001"
CTX = "ctx_site_claim_2"
REFUND_KEY = f"{CTX}:refund_request:claim:5"
REFUND = {"refund_amount": 900000, "idempotency_key": REFUND_KEY, "context_id": CTX}
REFUND_PATH = f"{BASE}/api/shop/payments/{ORDER}/refund"
CUSTOMER_TEXT = "가족 일정이 바뀌어 못 가게 됐어요"

Handler = Callable[[httpx.Request], httpx.Response]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def decided(status: str = "done", replayed: bool = False) -> httpx.Response:
    """누비투어 결정 200 응답 모양 (`view`)."""
    return httpx.Response(
        200,
        json={
            "id": "cl_01",
            "booking_code": ORDER,
            "status": status,
            "fee": 0,
            "refund_amount": 900000 if status == "done" else 0,
            "decided_at": "2026-11-23T03:00:00.000Z",
            "replayed": replayed,
        },
    )


def site_error(status: int, code: str) -> Handler:
    return lambda _r: httpx.Response(status, json={"error": {"code": code, "message": "거절"}})


class FakeSite:
    """누비투어 흉내: 취소 목록·예약 목록(쪽 넘김)과 취소 결정."""

    def __init__(self) -> None:
        self.reads: list[httpx.Request] = []
        self.writes: list[httpx.Request] = []
        self.decide: Handler = lambda _r: decided()
        self.pages = {
            "/api/v1/cancellations": {
                None: load("nubi_cancellations_p1.json"),
                "MjAy": load("nubi_cancellations_p2.json"),
            },
            "/api/v1/bookings": {
                None: load("nubi_bookings_p1.json"),
                "MjAy": load("nubi_bookings_p2.json"),
            },
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.reads.append(request)
            cursor = request.url.params.get("cursor")
            return httpx.Response(
                200, json=self.pages[request.url.path][cursor[:4] if cursor else None]
            )
        self.writes.append(request)
        return self.decide(request)


class FakeSafety:
    """safety 실행 기록 목록 흉내 (고객지원 `process_claim_queue`가 `execute()`에 보낸 모양)."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.amount = 900000
        self.status = "executed"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        record = {
            "execution_id": "exe_refund_9",
            "created_at": "2026-11-23T02:00:00Z",
            "request": {
                "action": "refund_request",
                "requester": "support",
                "context_id": CTX,
                "tenant_id": TENANT_ID_DEFAULT,
                "idempotency_key": REFUND_KEY,
                "params": {"claim_id": 5, "order_id": ORDER, "refund_amount": self.amount},
            },
            "decision": {"execution_id": "exe_refund_9", "grade": "C", "status": self.status},
        }
        return httpx.Response(200, json={"data": [record]})


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    fake = FakeSite()
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def safety(monkeypatch: pytest.MonkeyPatch) -> FakeSafety:
    fake = FakeSafety()
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety.test:8001")
    monkeypatch.setattr(gate_check, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_dir: Path,
    site: FakeSite,
    safety: FakeSafety,
) -> Iterator[TestClient]:
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv("AX_AGENT_TOKENS", "support=sup-tok,marketing=mkt-tok,dashboard-api=dash")
    monkeypatch.setenv("AX_SITE_API_TOKEN", SITE_TOKEN)
    monkeypatch.delenv(writes.WRITES_PER_HOUR_ENV, raising=False)
    monkeypatch.delenv(handoffs.DB_ENV, raising=False)
    handoffs.reset_store()
    # 누비투어 짝 + 신청 받은함 짝 + 쓰기 짝(문의 답변·환불), 환불 직접 쓰기 켬
    raw = json.loads((adapter_dir / "mapping.json").read_text(encoding="utf-8"))
    raw["inboxes"] = load("nubi_inboxes.json")
    raw["writes"] = load("nubi_writes.json")
    (adapter_dir / "mapping.json").write_text(json.dumps(raw), encoding="utf-8")
    (adapter_dir / writes.SWITCH_FILE).write_text(
        json.dumps({"enabled": ["refund"]}), encoding="utf-8"
    )
    yield TestClient(app)
    handoffs.reset_store()


def tasks() -> list[Any]:
    return handoffs.get_store().list(TENANT_ID_DEFAULT)


def ledger(adapter_dir: Path) -> dict[str, Any]:
    return json.loads((adapter_dir / writes.LEDGER_FILE).read_text(encoding="utf-8"))


def inbox(client: TestClient, **params: Any) -> httpx.Response:
    return client.get(CLAIMS_PATH, params={"state": "new", "limit": 20, **params}, headers=SUPPORT)


def refund(client: TestClient, **body: Any) -> httpx.Response:
    return client.post(REFUND_PATH, json={**REFUND, **body}, headers=SUPPORT)


# ---------------------------------------------------------------- 짝 파일


def test_nubi_files_load() -> None:
    found = m.SiteMapping.model_validate(
        {
            **load("nubi_mapping.json"),
            "inboxes": load("nubi_inboxes.json"),
            "writes": load("nubi_writes.json"),
        }
    )
    assert found.writes["refund"].partial_codes == ["REFUND_FAILED"]
    assert found.inboxes["claims"].path == "/api/v1/cancellations"


def test_bad_partial_code_is_refused() -> None:
    with pytest.raises(ValueError):
        m.WriteMap.model_validate(
            {"method": "POST", "path": "/a", "partial_codes": ["REFUND FAILED!"]}
        )


# ---------------------------------------------------------------- 신청 읽기 (state=new)


def test_new_claims_from_nubi_cancellations(client: TestClient, site: FakeSite) -> None:
    resp = inbox(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 결정 전(requested)만, 번호 순. 두 쪽을 끝까지 읽는다
    assert [r["key"] for r in body["data"]] == ["cl_01", "cl_03"]
    assert body["meta"]["total"] == 2 and body["meta"]["failed"] == 0
    first = body["data"][0]
    assert first == {
        "seq": 2,
        "key": "cl_01",
        "context_id": CTX,
        "buyer_id": "usr_01",
        "customer_name": "김*진",
        "order_id": ORDER,
        "type": "cancel",
        "reason": "other",
        "items": None,
        "unsealed": False,
        "evidence_count": 0,
        "description": CUSTOMER_TEXT,
        "created_at": "2026-11-23T01:00:00.000Z",
        # 사이트 규칙 예상 환불 (누비투어 `refund`·`fee`·`fee_rate` 그대로)
        "site_refund_amount": 650000,
        "site_fee": 250000,
        "site_fee_rate": 0.1,
    }
    cancel_reads = [r for r in site.reads if r.url.path == "/api/v1/cancellations"]
    assert len(cancel_reads) == 2  # 두 쪽
    assert cancel_reads[0].headers["authorization"] == f"Bearer {SITE_TOKEN}"


def test_site_refund_fields_empty_without_mapping() -> None:
    """사이트 규칙 칸 짝이 없으면 비운다 (고객지원은 책 쇼핑몰 규칙 그대로)."""
    raw = load("nubi_inboxes.json")["claims"]
    for name in claims.SITE_REFUND_FIELDS:
        raw["fields"].pop(name)
    res = m.ResourceMap.model_validate(raw)
    row = load("nubi_cancellations_p1.json")["data"][1]
    claim = claims.to_claim(row, res, {})
    line = claims.inbox_row({**claim, "seq": 2})
    assert {k: line[k] for k in claims.SITE_REFUND_FIELDS} == {
        "site_refund_amount": None,
        "site_fee": None,
        "site_fee_rate": None,
    }


def test_new_claims_page_by_limit(client: TestClient) -> None:
    body = inbox(client, limit=1).json()
    assert [r["key"] for r in body["data"]] == ["cl_01"]
    assert body["meta"]["total"] == 2


def test_numbers_are_stable_and_links_are_kept(client: TestClient, adapter_dir: Path) -> None:
    first = [r["seq"] for r in inbox(client).json()["data"]]
    up.clear_cache()
    again = [r["seq"] for r in inbox(client).json()["data"]]
    assert first == again == [2, 3]
    assert links.find("claims", "1") == "cl_02"
    assert links.find("claims", "2") == "cl_01"
    assert links.find("claims", "3") == "cl_03"
    # 결정 전 신청만 결제 번호(= 예약 번호) 짝을 가진다
    assert links.find("payments", ORDER) == "cl_01"
    assert links.find("payments", "NB-20261115-0003") == "cl_03"
    assert links.find("payments", "NB-20261111-0002") is None


def test_guest_booking_gets_guest_buyer() -> None:
    res = m.ResourceMap.model_validate(load("nubi_inboxes.json")["claims"])
    row = load("nubi_cancellations_p1.json")["data"][0]
    guest = load("nubi_bookings_p1.json")["data"][1]
    claim = claims.to_claim(row, res, {"NB-20261111-0002": guest})
    assert claim["buyer_id"] == "guest-NB-20261111-0002"
    assert claim["status"] == "closed"
    # 주문을 못 찾아도 빈 칸으로 두고 지어내지 않는다
    lone = claims.to_claim(row, res, {})
    assert (lone["buyer_id"], lone["customer_name"]) == ("guest-NB-20261111-0002", "")


def test_claim_result_moves_claim_from_new_to_open(client: TestClient, adapter_dir: Path) -> None:
    inbox(client)
    view = {"id": 5, "status": "needs_review", "expected_refund": 900000}
    resp = client.post(
        f"{CLAIMS_PATH}/2/result",
        json={"support_claim_id": 5, "status": "needs_review", "result": view},
        headers=SUPPORT,
    )
    assert resp.status_code == 200, resp.text
    up.clear_cache()
    assert [r["key"] for r in inbox(client).json()["data"]] == ["cl_03"]
    opened = client.get(CLAIMS_PATH, params={"state": "open"}, headers=SUPPORT).json()
    # 고객지원 `inquiry_pull.claim_digest`와 같은 지문이라 같은 결과면 다시 쓰지 않는다
    text = json.dumps(view, ensure_ascii=False, sort_keys=True)
    want = hashlib.sha1(text.encode()).hexdigest()[:16]
    assert opened["data"] == [
        {"seq": 2, "support_claim_id": 5, "status": "needs_review", "result_digest": want}
    ]
    assert opened["meta"]["next_after"] is None
    # 처리 기록에는 지문만 (결과 본문 값은 없다)
    stored = (adapter_dir / claims.STATE_FILE).read_text(encoding="utf-8")
    assert "900000" not in stored and "expected_refund" not in stored


def test_open_claims_page_and_finished_are_left_out(client: TestClient) -> None:
    for seq, status in [(1, "completed"), (2, "needs_review"), (3, "approved"), (4, "rejected")]:
        client.post(
            f"{CLAIMS_PATH}/{seq}/result",
            json={"support_claim_id": seq + 10, "status": status, "result": {"s": status}},
            headers=SUPPORT,
        )
    first = client.get(CLAIMS_PATH, params={"state": "open", "limit": 1}, headers=SUPPORT).json()
    assert [r["seq"] for r in first["data"]] == [2] and first["meta"]["next_after"] == 2
    rest = client.get(
        CLAIMS_PATH, params={"state": "open", "after": 2, "limit": 1}, headers=SUPPORT
    ).json()
    assert [r["seq"] for r in rest["data"]] == [3] and rest["meta"]["next_after"] is None


def test_failed_is_bookkeeping_only(client: TestClient, adapter_dir: Path) -> None:
    inbox(client)
    resp = client.post(f"{CLAIMS_PATH}/2/failed", json={"reason": "ERROR"}, headers=SUPPORT)
    assert resp.status_code == 200
    assert not (adapter_dir / claims.STATE_FILE).exists()
    up.clear_cache()
    assert [r["key"] for r in inbox(client).json()["data"]] == ["cl_01", "cl_03"]


def test_inbox_needs_token_and_mapping(client: TestClient, adapter_dir: Path) -> None:
    assert client.get(CLAIMS_PATH).status_code in (401, 403)  # 접수 동기화 기록과 같은 인증
    bad = client.get(CLAIMS_PATH, headers={"authorization": "Bearer nope"})
    assert bad.status_code == 403
    assert inbox(client, state="weird").status_code == 422
    raw = json.loads((adapter_dir / "mapping.json").read_text(encoding="utf-8"))
    raw.pop("inboxes")
    (adapter_dir / "mapping.json").write_text(json.dumps(raw), encoding="utf-8")
    resp = inbox(client)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_MAPPED"


# ---------------------------------------------------------------- 주문 하나 (고객지원 판정용 칸)


def test_order_detail_has_claim_fields(client: TestClient) -> None:
    data = client.get(f"{BASE}/api/shop/orders/{ORDER}").json()["data"]
    assert [p["payment_id"] for p in data["payments"]] == [ORDER]
    assert [(i["order_item_id"], i["title"]) for i in data["items"]] == [
        (f"{ORDER}-1", "JP-OSA-4D")
    ]
    assert data["buyer_id"] == "usr_01"
    guest = client.get(f"{BASE}/api/shop/orders/NB-20261111-0002").json()["data"]
    assert guest["buyer_id"] == "guest-NB-20261111-0002"


# ---------------------------------------------------------------- 환불 = 취소 결정


def test_refund_becomes_nubi_approve(client: TestClient, site: FakeSite, adapter_dir: Path) -> None:
    inbox(client)
    resp = refund(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "payment_id": ORDER,
        "refund_amount": 900000,
        "applied": "site",
    }
    (call,) = site.writes
    assert call.method == "POST"
    assert call.url == httpx.URL(SITE + "/api/v1/cancellations/cl_01/decision")
    assert call.headers["authorization"] == f"Bearer {SITE_TOKEN}"
    body = json.loads(call.content)
    # 금액은 안전 관문 기록 금액 그대로. 사이트 수수료와 달라도 되게 사유를 늘 싣는다
    assert body == {
        "decision": "approve",
        "refund_amount": 900000,
        "reason": "AXcellent 안전 관문에서 담당자가 승인한 환불 금액이에요",
    }
    assert tasks() == []
    (entry,) = ledger(adapter_dir).values()
    assert (entry["status"], entry["execution_id"]) == ("done", "exe_refund_9")
    assert entry["site_path"] == "/api/v1/cancellations/{site_id}/decision"


def test_site_rule_amount_goes_to_decision_body(
    client: TestClient, site: FakeSite, safety: FakeSafety
) -> None:
    """사이트 규칙 환불액(받은 900000원 중 650000원)이 관문 기록·결정 본문까지 같은 금액이다.

    누비투어는 수수료 = 받은 금액 − 환불 금액(900000 − 650000 = 250000)으로 정해 규칙 수수료와 같다.
    """
    row = inbox(client).json()["data"][0]
    amount = row["site_refund_amount"]
    assert amount == 650000
    safety.amount = amount  # 고객지원이 판정 금액 그대로 `execute("refund_request")`에 보낸 값
    resp = refund(client, refund_amount=amount)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["refund_amount"] == 650000
    body = json.loads(site.writes[0].content)
    assert body["decision"] == "approve" and body["refund_amount"] == 650000
    paid = load("nubi_cancellations_p1.json")["data"][1]["paid"]
    assert paid - body["refund_amount"] == row["site_fee"]


def test_full_amount_against_site_rule_gate_is_refused(
    client: TestClient, site: FakeSite, safety: FakeSafety
) -> None:
    """관문 기록이 사이트 규칙 금액(650000원)인데 전액(900000원)을 보내면 사이트를 부르지 않는다."""
    inbox(client)
    safety.amount = 650000
    resp = refund(client)
    assert resp.status_code != 200
    assert site.writes == []


def test_decision_body_meets_nubi_schema(client: TestClient, site: FakeSite) -> None:
    """누비투어 `z.object({decision: enum, refund_amount?: int >= 0, reason?: max 500})`."""
    inbox(client)
    refund(client)
    body = json.loads(site.writes[0].content)
    assert set(body) <= {"decision", "refund_amount", "reason"}
    assert body["decision"] in ("approve", "reject")
    assert isinstance(body["refund_amount"], int) and body["refund_amount"] >= 0
    assert isinstance(body["reason"], str) and 1 <= len(body["reason"].strip()) <= 500


def test_replayed_decision_is_done(client: TestClient, site: FakeSite) -> None:
    """사이트가 같은 결정을 이미 했으면(`replayed: true`) 그것도 반영 끝이다."""
    inbox(client)
    site.decide = lambda _r: decided(replayed=True)
    assert refund(client).json()["data"]["applied"] == "site"
    assert tasks() == []


def test_same_refund_twice_calls_site_once(client: TestClient, site: FakeSite) -> None:
    inbox(client)
    first = refund(client).json()
    again = refund(client).json()
    assert len(site.writes) == 1
    assert again["data"] == first["data"] and again["meta"]["replayed"] is True


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "REASON_REQUIRED"),
        (400, "BAD_REFUND"),
        (404, "NOT_FOUND"),
        (409, "ALREADY_DECIDED"),
        (409, "BAD_STATE"),
    ],
)
def test_site_refusals_are_site_rejected(
    client: TestClient, site: FakeSite, adapter_dir: Path, status: int, code: str
) -> None:
    inbox(client)
    site.decide = site_error(status, code)
    resp = refund(client)
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "SITE_REJECTED"
    assert (error["site_code"], error["site_status"]) == (code, status)
    assert tasks() == []
    (entry,) = ledger(adapter_dir).values()
    assert (entry["status"], entry["site_code"]) == ("rejected", code)
    # 다시 보내도 사이트에 또 보내지 않고 같은 거절
    assert refund(client).status_code == 409 and len(site.writes) == 1


def test_refund_failed_is_partial_not_unknown(
    client: TestClient, site: FakeSite, adapter_dir: Path
) -> None:
    """502 `REFUND_FAILED`: 취소 결정은 반영, 결제사 환불만 실패 → 할 일함 "결제사 환불 확인"."""
    inbox(client)
    site.decide = site_error(502, "REFUND_FAILED")
    resp = refund(client)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["applied"] == "pending_human"  # 돈은 아직 안 나갔다: 고객지원은 보류로 둔다
    (task,) = tasks()
    assert task.skill == "site_apply_refund"
    assert task.reason == writes.REASONS["site_refund_failed"]
    assert task.input["site_code"] == "REFUND_FAILED" and task.input["site_status"] == 502
    assert "idempotency_key" not in task.input
    (entry,) = ledger(adapter_dir).values()
    assert (entry["status"], entry["reason"]) == ("partial", "site_refund_failed")
    assert entry["site_code"] == "REFUND_FAILED"
    # 다시 와도 사이트에 또 보내지 않고 같은 할 일
    again = refund(client).json()
    assert again["data"]["handoff_task_id"] == data["handoff_task_id"]
    assert len(site.writes) == 1 and len(tasks()) == 1


def test_other_502_is_unknown(client: TestClient, site: FakeSite, adapter_dir: Path) -> None:
    inbox(client)
    site.decide = site_error(502, "UPSTREAM_DOWN")
    assert refund(client).json()["data"]["applied"] == "pending_human"
    (entry,) = ledger(adapter_dir).values()
    assert entry["status"] == "unknown"


@pytest.mark.parametrize("status", [401, 403])
def test_key_without_write_scope_goes_to_inbox(
    client: TestClient, site: FakeSite, adapter_dir: Path, status: int
) -> None:
    inbox(client)
    site.decide = site_error(status, "FORBIDDEN")
    assert refund(client).json()["data"]["applied"] == "pending_human"
    assert "refund" in direct.denied_actions()
    (task,) = tasks()
    assert task.reason == writes.REASONS["no_write_permission"]


@pytest.mark.parametrize("kind", ["timeout", "429", "500"])
def test_timeout_and_5xx_are_unknown_and_not_retried(
    client: TestClient, site: FakeSite, adapter_dir: Path, kind: str
) -> None:
    inbox(client)

    def boom(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(int(kind))

    site.decide = boom
    assert refund(client).json()["data"]["applied"] == "pending_human"
    assert len(site.writes) == 1  # 결정은 멱등이지만 W3 규칙대로 다시 보내지 않는다
    (entry,) = ledger(adapter_dir).values()
    assert entry["status"] == "unknown"
    (task,) = tasks()
    assert task.reason == writes.REASONS["site_unknown"]


def test_no_link_goes_to_inbox(client: TestClient, site: FakeSite, adapter_dir: Path) -> None:
    """신청 받은함을 아직 읽지 않아 결제 번호 → 신청 짝이 없으면 지어내지 않고 할 일함."""
    resp = refund(client)
    assert resp.json()["data"]["applied"] == "pending_human"
    assert site.writes == []
    (entry,) = ledger(adapter_dir).values()
    assert entry["reason"] == "link_missing"


@pytest.mark.parametrize("change", ["amount", "not_executed", "body_amount"])
def test_gate_failure_never_calls_site(
    client: TestClient, site: FakeSite, safety: FakeSafety, adapter_dir: Path, change: str
) -> None:
    inbox(client)
    body: dict[str, Any] = {}
    if change == "amount":
        safety.amount = 650000  # 관문이 승인한 금액과 다르다
    elif change == "not_executed":
        safety.status = "pending_review"
    else:
        body = {"refund_amount": 1000000}
    resp = refund(client, **body)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert site.writes == []
    assert tasks() == []
    assert not (adapter_dir / writes.LEDGER_FILE).exists()


# ---------------------------------------------------------------- 기록에 남기지 않는 것


def test_files_and_logs_hold_no_key_or_personal_text(
    client: TestClient, site: FakeSite, adapter_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="ax.site_adapter")
    inbox(client)
    client.post(
        f"{CLAIMS_PATH}/2/result",
        json={"support_claim_id": 5, "status": "approved", "result": {"customer": "김*진"}},
        headers=SUPPORT,
    )
    site.decide = site_error(502, "REFUND_FAILED")
    assert refund(client).json()["data"]["applied"] == "pending_human"
    stored = "".join(
        p.read_text(encoding="utf-8") for p in adapter_dir.iterdir() if p.suffix == ".json"
    )
    for raw in (SITE_TOKEN, "sup-tok", REFUND_KEY, CUSTOMER_TEXT, "김*진", "박*수"):
        assert raw not in stored, raw
        assert raw not in caplog.text, raw
