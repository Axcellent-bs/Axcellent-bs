"""사이트 변환기 쓰기 (W1 뼈대·W2 관문 확인): 판정 1·2·3·4·6, 할 일함 넣기, 쓰기 장부.

설계 "③-쓰기"·"② 사람 반영". 이 파일은 고객사에 쓰지 않는 길(할 일함)만 본다. 직접 쓰기(W3)는
`test_direct_writes.py`. safety는 실행 기록 응답을 흉내 낸다(`gate_check.TRANSPORT`).
"""

import copy
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_contracts.schemas import TENANT_ID_DEFAULT
from ax_dashboard_api import handoffs
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import gate_check, writes
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
SAFETY_URL = "http://safety.test:8001"
BASE = "/site-adapter"
SUPPORT = {"authorization": "Bearer sup-tok"}
MARKETING = {"authorization": "Bearer mkt-tok"}
INFRA = {"authorization": "Bearer inf-tok"}
SHOP_KEY = {"authorization": "Bearer shop-key-1"}
HUMAN = {"authorization": "Bearer test-human-token"}
SITE_TOKEN = "site-secret-token-9"
REFUND_KEY = "ctx_shop_abc:refund_request:claim:1"
REFUND = {"refund_amount": 23000, "idempotency_key": REFUND_KEY, "context_id": "ctx_shop_abc"}
COUPON_KEY = "ctx_a:issue_coupon:buy_1"


def execution(
    execution_id: str,
    action: str,
    requester: str,
    context_id: str,
    key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """safety `GET /api/safety/executions` 한 줄 모양 (`ExecutionRecord`)."""
    return {
        "execution_id": execution_id,
        "created_at": "2026-11-23T01:00:00Z",
        "request": {
            "action": action,
            "requester": requester,
            "context_id": context_id,
            "tenant_id": TENANT_ID_DEFAULT,
            "idempotency_key": key,
            "params": params,
        },
        "decision": {"execution_id": execution_id, "grade": "A", "status": "executed"},
    }


# 도메인이 `execute()`에 보낸 것과 같은 모양 (고객지원 claim_handlers, 마케팅 handlers)
REFUND_RECORD = execution(
    "exe_refund_1",
    "refund_request",
    "support",
    "ctx_shop_abc",
    REFUND_KEY,
    {"claim_id": 1, "order_id": "ord_1", "buyer_id": "buy_1", "refund_amount": 23000},
)
COUPON_RECORD = execution(
    "exe_coupon_1",
    "issue_coupon",
    "marketing",
    "ctx_a",
    COUPON_KEY,
    {
        "purpose_code": "compensation",
        "buyer_id": "buy_1",
        "count": 2,
        "unit_value": 1500,
        "campaign_id": None,
        "total_value": 3000,
    },
)


class FakeSafety:
    """safety 실행 기록 목록 흉내. `mode`: ok·down(연결 오류)·5xx·bad(이상한 본문)."""

    def __init__(self) -> None:
        self.records = [copy.deepcopy(REFUND_RECORD), copy.deepcopy(COUPON_RECORD)]
        self.calls: list[httpx.Request] = []
        self.mode = "ok"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.mode == "down":
            raise httpx.ConnectError("safety down", request=request)
        if self.mode == "5xx":
            return httpx.Response(503)
        if self.mode == "bad":
            return httpx.Response(200, json={"data": {"oops": True}})
        assert request.url.path == "/api/safety/executions"
        ctx = request.url.params.get("context_id")
        rows = [r for r in self.records if r["request"]["context_id"] == ctx]
        return httpx.Response(200, json={"data": rows, "error": None})

    def record(self, action: str) -> dict[str, Any]:
        wanted = gate_check.SAFETY_ACTION[action]
        return next(r for r in self.records if r["request"]["action"] == wanted)


@pytest.fixture
def safety(monkeypatch: pytest.MonkeyPatch) -> FakeSafety:
    fake = FakeSafety()
    monkeypatch.setenv("AX_SAFETY_URL", SAFETY_URL)
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    monkeypatch.setattr(gate_check, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path, safety: FakeSafety
) -> Iterator[TestClient]:
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(
        "AX_AGENT_TOKENS",
        "support=sup-tok,marketing=mkt-tok,infra=inf-tok,dashboard-api=dash-tok",
    )
    monkeypatch.setenv("AX_SHOP_CLIENT_KEYS", "inquiry:shop-key-1")
    monkeypatch.setenv("AX_SITE_API_TOKEN", SITE_TOKEN)
    monkeypatch.delenv(handoffs.DB_ENV, raising=False)
    handoffs.reset_store()
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    yield TestClient(app)
    handoffs.reset_store()
    assert calls == []  # 이 파일의 시험은 고객사에 쓰지 않는다 (직접 쓰기는 test_direct_writes.py)


def tasks() -> list[Any]:
    return handoffs.get_store().list(TENANT_ID_DEFAULT)


def ledger(adapter_dir: Path) -> dict[str, Any]:
    return json.loads((adapter_dir / writes.LEDGER_FILE).read_text(encoding="utf-8"))


# 동작마다 (메서드, 경로, 본문, 허용 토큰)
CASES: dict[str, tuple[str, str, dict[str, Any] | None, dict[str, str]]] = {
    "refund": ("POST", "/api/shop/payments/pay_0001/refund", REFUND, SUPPORT),
    "coupon_issue": (
        "POST",
        "/api/shop/coupons",
        {
            "idempotency_key": COUPON_KEY,
            "purpose_code": "compensation",
            "buyer_id": "buy_1",
            "count": 2,
            "unit_value": 1500,
            "campaign_id": None,
            "context_id": "ctx_a",
        },
        MARKETING,
    ),
    "banner_post": (
        "POST",
        "/api/shop/banners",
        {
            "notice_id": "ntc_1",
            "text": "결제가 잠시 늦어요",
            "valid_from": None,
            "valid_to": None,
            "context_id": "ctx_inc",
        },
        MARKETING,
    ),
    "banner_end": ("DELETE", "/api/shop/banners/ntc_1", None, MARKETING),
    "claim_result": (
        "POST",
        "/api/shop/admin/claims/7/result",
        {"support_claim_id": 3, "status": "approved", "result": {"refund": 23000}},
        SUPPORT,
    ),
    "inquiry_answer": (
        "POST",
        "/api/shop/admin/inquiries/9/view",
        {"view": {"id": 4, "status": "answered"}, "view_digest": "dg1"},
        SUPPORT,
    ),
    "payment_rollback": (
        "POST",
        "/api/shop/admin/payment-version/rollback",
        {"to_version": "v1"},
        INFRA,
    ),
    "fault_release": ("DELETE", "/api/shop/admin/faults", None, INFRA),
}


def send(
    client: TestClient, action: str, headers: dict[str, str] | None = None, **body: Any
) -> httpx.Response:
    method, path, base, allowed = CASES[action]
    payload = {**base, **body} if base is not None else None
    return client.request(method, BASE + path, json=payload, headers=headers or allowed)


@pytest.mark.parametrize("action", list(CASES))
def test_every_write_goes_to_the_inbox(client: TestClient, adapter_dir: Path, action: str) -> None:
    resp = send(client, action)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["applied"] == "pending_human"
    (task,) = tasks()
    assert data["handoff_task_id"] == task.task_id
    assert task.skill == f"site_apply_{action}"
    assert task.to_service == writes.ALLOWED_SERVICE[action]  # 보낸 AI
    assert task.from_service == "site-adapter"
    assert task.input["title"] == writes.TITLES[action]
    (entry,) = ledger(adapter_dir).values()
    assert (entry["action"], entry["status"], entry["handoff_task_id"]) == (
        action,
        "handoff",
        task.task_id,
    )
    assert entry["reason"] == "no_write_map"  # 누비투어 짝에는 writes가 없다 (판정 3)


@pytest.mark.parametrize("action", [a for a in CASES if a != "inquiry_answer"])
def test_other_service_is_403_and_not_queued(
    client: TestClient, adapter_dir: Path, action: str
) -> None:
    """판정 1: 다른 에이전트·판매 서버 서비스 키·사람 토큰은 403이고 할 일함에도 안 넣는다."""
    wrong = MARKETING if writes.ALLOWED_SERVICE[action] != "marketing" else SUPPORT
    for headers in (wrong, SHOP_KEY, HUMAN):
        resp = send(client, action, headers=headers)
        assert resp.status_code == 403, (headers, resp.text)
        assert resp.json()["error"]["code"] == "AGENT_NOT_ALLOWED"
    assert tasks() == []
    assert not (adapter_dir / writes.LEDGER_FILE).exists()


def test_inquiry_view_from_other_writer_is_recorded_only(
    client: TestClient, adapter_dir: Path
) -> None:
    """문의 화면 값은 접수 동기화 기록 길이라 예전 인증 그대로: 다른 에이전트·판매 서버 서비스
    키가 보낸 것은 예전처럼 기록만 하고(할 일 없음), 사람 토큰은 예전처럼 403."""
    for headers in (MARKETING, SHOP_KEY):
        resp = send(client, "inquiry_answer", headers=headers)
        assert resp.status_code == 200 and resp.json()["data"] == {"ok": True}
    assert send(client, "inquiry_answer", headers=HUMAN).status_code == 403
    assert tasks() == []
    lines = (adapter_dir / "bookkeeping.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_inquiry_view_keeps_the_sync_record(client: TestClient, adapter_dir: Path) -> None:
    resp = send(client, "inquiry_answer")
    assert resp.json()["data"]["ok"] is True
    line = json.loads((adapter_dir / "bookkeeping.jsonl").read_text(encoding="utf-8"))
    assert (line["kind"], line["key"], line["action"]) == ("inquiries", "9", "view")
    assert line["fields"] == ["view", "view_digest"]


def test_other_sync_records_are_unchanged(client: TestClient) -> None:
    for path in ("inquiries/7/synced", "claims/7/failed", "ideas/2/synced", "claims/7/retry"):
        resp = client.post(f"{BASE}/api/shop/admin/{path}", json={}, headers=SHOP_KEY)
        assert resp.status_code == 200 and resp.json()["data"] == {"ok": True}
    assert tasks() == []


def test_same_key_twice_is_one_task(client: TestClient, adapter_dir: Path) -> None:
    """판정 6: 같은 키·같은 본문은 앞 결과(같은 할 일)를 그대로."""
    first = send(client, "refund").json()
    again = send(client, "refund").json()
    assert again["data"] == first["data"]
    assert first["meta"]["replayed"] is False and again["meta"]["replayed"] is True
    assert len(tasks()) == 1 and len(ledger(adapter_dir)) == 1


def test_same_key_different_body_is_409(client: TestClient) -> None:
    send(client, "refund")
    # 금액은 같아 관문 기록 확인(판정 2)은 지나지만 본문이 다르다
    resp = send(client, "refund", cancel_order=True)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # 금액까지 다르면 판정 2(멱등보다 앞)에서 403
    resp = send(client, "refund", refund_amount=99000)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert len(tasks()) == 1


def test_same_key_value_in_other_action_does_not_collide(client: TestClient) -> None:
    """서비스·동작이 다르면 멱등 키 값이 같아도(같은 notice_id) 다른 일이다."""
    send(client, "banner_post")
    send(client, "banner_end")
    assert len(tasks()) == 2


def test_missing_idempotency_key_is_422(client: TestClient) -> None:
    resp = client.post(
        f"{BASE}/api/shop/payments/pay_0001/refund", json={"refund_amount": 1}, headers=SUPPORT
    )
    assert resp.status_code == 422 and tasks() == []


def test_left_sending_goes_to_inbox_without_resending(
    client: TestClient, adapter_dir: Path
) -> None:
    """보내는 도중 죽어 sending이 남은 쓰기는 다시 보내지 않고 사람에게 확인을 맡긴다."""
    send(client, "refund")
    book = ledger(adapter_dir)
    (key,) = book
    book[key]["status"] = "sending"
    (adapter_dir / writes.LEDGER_FILE).write_text(json.dumps(book), encoding="utf-8")
    resp = send(client, "refund")
    assert resp.json()["data"]["applied"] == "pending_human"
    assert ledger(adapter_dir)[key]["reason"] == "left_unfinished"
    assert len(tasks()) == 1  # 할 일함도 같은 멱등 키라 같은 일


def _with_writes(adapter_dir: Path, **actions: Any) -> None:
    path = adapter_dir / "mapping.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["writes"] = actions
    path.write_text(json.dumps(raw), encoding="utf-8")


ANSWER_MAP = {
    "method": "POST",
    "path": "/api/v1/inquiries/{site_id}/answer",
    "path_params": {"site_id": "link:inquiries.seq"},
    "body": {"answer": "view.answer"},
    "ok": [200],
}
REFUND_MAP = {"method": "POST", "path": "/api/v1/refunds", "body": {"amount": "refund_amount"}}


def test_write_map_present_but_switch_off(client: TestClient, adapter_dir: Path) -> None:
    """판정 4: 짝이 있어도 회사 연결에서 그 동작을 켜지 않았으면(기본 꺼짐) 할 일함."""
    _with_writes(adapter_dir, inquiry_answer=ANSWER_MAP)
    send(client, "inquiry_answer")
    (entry,) = ledger(adapter_dir).values()
    assert entry["reason"] == "direct_off"


def test_switch_on_is_per_action(client: TestClient, adapter_dir: Path) -> None:
    _with_writes(adapter_dir, inquiry_answer=ANSWER_MAP, refund=REFUND_MAP)
    (adapter_dir / writes.SWITCH_FILE).write_text(
        json.dumps({"enabled": ["inquiry_answer", "refund"]}), encoding="utf-8"
    )
    assert writes.direct_write_on("inquiry_answer") is True
    assert writes.direct_write_on("claim_result") is False  # 켠 동작만
    # 돈이 걸린 동작은 관문 기록을 확인한 쓰기(execution_id)에서만 켜진 것으로 본다
    assert writes.direct_write_on("refund") is False
    assert writes.direct_write_on("refund", "exe_refund_1") is True
    assert writes.route("inquiry_answer") == writes.DIRECT_READY
    assert writes.route("refund", "exe_refund_1") == writes.DIRECT_READY
    # 켰지만 문의 번호 짝(link)이 없어 고객사에 보내지 않고 할 일함 (W3).
    # 고객사에 실제로 쓰는 경우는 test_direct_writes.py가 본다 (이 파일의 client는 호출 0번)
    send(client, "inquiry_answer")
    reasons = {e["action"]: e["reason"] for e in ledger(adapter_dir).values()}
    assert reasons == {"inquiry_answer": "link_missing"}


def test_money_actions_always_go_to_inbox(client: TestClient, adapter_dir: Path) -> None:
    assert writes.route("refund") == "no_write_map"
    _with_writes(adapter_dir, refund=REFUND_MAP)
    assert writes.route("refund") == "direct_off"
    assert send(client, "refund").json()["data"]["applied"] == "pending_human"


def test_broken_mapping_still_goes_to_inbox(client: TestClient, adapter_dir: Path) -> None:
    (adapter_dir / "mapping.json").write_text(json.dumps({"version": 2}), encoding="utf-8")
    assert send(client, "refund").json()["data"]["applied"] == "pending_human"
    (entry,) = ledger(adapter_dir).values()
    assert entry["reason"] == "mapping_broken"


def test_coupon_id_is_the_task_id(client: TestClient) -> None:
    """마케팅은 `coupon_id`를 필수로 본다: 할 일 id로 채운다 (설계 ②)."""
    data = send(client, "coupon_issue").json()["data"]
    assert data["coupon_id"] == data["handoff_task_id"]
    assert (data["buyer_id"], data["count"], data["unit_value"]) == ("buy_1", 2, 1500)


def test_refund_response_does_not_claim_money_moved(client: TestClient) -> None:
    data = send(client, "refund").json()["data"]
    assert "refund_id" not in data and "order_cancelled" not in data
    (task,) = tasks()
    assert (task.input["payment_id"], task.input["refund_amount"]) == ("pay_0001", 23000)
    assert task.context_id == "ctx_shop_abc"


def test_infra_responses_do_not_look_done(client: TestClient) -> None:
    assert send(client, "fault_release").json()["data"]["released"] is False
    done = send(client, "payment_rollback").json()["data"]
    assert (done["before"], done["version"]) == (None, None)


def test_ledger_and_logs_hold_no_raw_body_or_keys(
    client: TestClient, adapter_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_text = "고객이 쓴 비밀 문장"
    caplog.set_level(logging.DEBUG, logger="ax.site_adapter")
    send(client, "refund")
    send(client, "inquiry_answer", view={"id": 4, "status": "answered", "messages": [secret_text]})
    assert send(client, "refund", cancel_order=True).status_code == 409  # 409도 본문을 안 남김
    assert send(client, "refund", refund_amount=1).status_code == 403  # 관문 실패도 마찬가지
    text = (adapter_dir / writes.LEDGER_FILE).read_text(encoding="utf-8")
    for raw in (REFUND_KEY, secret_text, SITE_TOKEN, "sup-tok", "dash-tok"):
        assert raw not in text
        assert raw not in caplog.text
    task = next(t for t in tasks() if t.skill == "site_apply_inquiry_answer")
    assert secret_text not in json.dumps(task.input, ensure_ascii=False)


def test_old_mapping_without_writes_still_loads(adapter_dir: Path) -> None:
    found = m.load_mapping()
    assert found is not None and found.writes == {}


def test_unmapped_write_is_still_404(client: TestClient) -> None:
    resp = client.post(f"{BASE}/api/shop/sellers/s1/payouts", json={}, headers=SUPPORT)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_MAPPED"


# ---------------------------------------------------------------- 판정 2 (W2): 관문 기록 확인

MONEY = ("refund", "coupon_issue")


def assert_nothing_kept(adapter_dir: Path) -> None:
    """통과 못 한 쓰기는 할 일함·장부에 남지 않는다 (고객사 호출 0번은 `client`가 본다)."""
    assert tasks() == []
    assert not (adapter_dir / writes.LEDGER_FILE).exists()


@pytest.mark.parametrize(
    ("action", "execution_id"), [("refund", "exe_refund_1"), ("coupon_issue", "exe_coupon_1")]
)
def test_gate_passed_keeps_execution_id(
    client: TestClient, adapter_dir: Path, safety: FakeSafety, action: str, execution_id: str
) -> None:
    resp = send(client, action)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["applied"] == "pending_human"
    (entry,) = ledger(adapter_dir).values()
    assert entry["execution_id"] == execution_id
    (call,) = safety.calls
    # 실행 기록은 context_id로 찾고, dashboard-api 에이전트 토큰을 붙인다
    body = CASES[action][2]
    assert body is not None and call.url.params["context_id"] == body["context_id"]
    assert call.headers["authorization"] == "Bearer dash-tok"


def test_non_money_writes_do_not_ask_safety(
    client: TestClient, adapter_dir: Path, safety: FakeSafety
) -> None:
    for action in ("banner_post", "claim_result", "inquiry_answer", "fault_release"):
        assert send(client, action).status_code == 200
    assert safety.calls == []
    assert all(e["execution_id"] is None for e in ledger(adapter_dir).values())


@pytest.mark.parametrize("action", MONEY)
@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda r: r["request"].update(action="publish_notice"), id="action_differs"),
        pytest.param(lambda r: r["request"].update(requester="infra"), id="requester_differs"),
        pytest.param(
            lambda r: r["decision"].update(status="held", hold_reason="amount_over"), id="held"
        ),
        pytest.param(lambda r: r["decision"].update(status="verifying"), id="verifying"),
        pytest.param(lambda r: r["decision"].update(status="rejected"), id="rejected"),
        pytest.param(lambda r: r["request"].update(idempotency_key="other"), id="no_record"),
        pytest.param(lambda r: r["request"].update(tenant_id="other-co"), id="other_tenant"),
    ],
)
def test_gate_record_mismatch_is_403(
    client: TestClient, adapter_dir: Path, safety: FakeSafety, action: str, spoil: Any
) -> None:
    spoil(safety.record(action))
    resp = send(client, action)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert_nothing_kept(adapter_dir)


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("refund", {"refund_amount": 22000}),
        ("refund", {"refund_amount": "23000"}),  # 글자는 금액이 아니다
        ("refund", {"refund_amount": None}),
        ("coupon_issue", {"count": 3}),
        ("coupon_issue", {"count": 3, "unit_value": 1000}),  # 합계만 같아도 다른 쿠폰
        ("coupon_issue", {"unit_value": 2000}),
        ("coupon_issue", {"buyer_id": "buy_2"}),
    ],
)
def test_gate_amount_mismatch_is_403(
    client: TestClient, adapter_dir: Path, action: str, body: dict[str, Any]
) -> None:
    resp = send(client, action, **body)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert_nothing_kept(adapter_dir)


def test_coupon_record_total_must_match(
    client: TestClient, adapter_dir: Path, safety: FakeSafety
) -> None:
    safety.record("coupon_issue")["request"]["params"]["total_value"] = 9999
    assert send(client, "coupon_issue").json()["error"]["code"] == "GATE_NOT_PASSED"
    assert_nothing_kept(adapter_dir)


@pytest.mark.parametrize("action", MONEY)
def test_gate_without_context_id_is_403(
    client: TestClient, adapter_dir: Path, safety: FakeSafety, action: str
) -> None:
    resp = send(client, action, context_id=None)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert safety.calls == []  # 찾을 길이 없어 safety를 부르지 않는다
    assert_nothing_kept(adapter_dir)


@pytest.mark.parametrize("action", MONEY)
@pytest.mark.parametrize("mode", ["down", "5xx", "bad"])
def test_safety_error_is_503_and_not_written(
    client: TestClient, adapter_dir: Path, safety: FakeSafety, action: str, mode: str
) -> None:
    """safety에 닿지 못하면 통과가 아니다(fail-closed). 403과 구분해 503."""
    safety.mode = mode
    resp = send(client, action)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "GATE_UNAVAILABLE"
    assert_nothing_kept(adapter_dir)


def test_no_agent_token_is_503(
    client: TestClient, adapter_dir: Path, safety: FakeSafety, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_AGENT_TOKENS", "support=sup-tok,marketing=mkt-tok,infra=inf-tok")
    monkeypatch.setattr(gate_check.config, "is_mock", lambda name=None: False)
    resp = send(client, "refund")
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "GATE_UNAVAILABLE"
    assert safety.calls == []
    assert_nothing_kept(adapter_dir)


def test_replay_is_checked_again(client: TestClient, adapter_dir: Path, safety: FakeSafety) -> None:
    """같은 쓰기를 다시 보내도 판정 2를 다시 본다: 그 사이 기록이 바뀌면 받지 않는다."""
    assert send(client, "refund").status_code == 200
    safety.record("refund")["decision"]["status"] = "rejected"
    assert send(client, "refund").json()["error"]["code"] == "GATE_NOT_PASSED"
    assert len(tasks()) == 1 and len(ledger(adapter_dir)) == 1


def test_gate_failure_logs_hold_no_key_or_body(
    client: TestClient, safety: FakeSafety, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="ax.site_adapter")
    send(client, "refund", refund_amount=22000)
    send(client, "coupon_issue", buyer_id="buy_secret_77")
    safety.mode = "down"
    send(client, "refund")
    assert "관문 기록" in caplog.text  # 경고는 남는다
    for raw in (REFUND_KEY, COUPON_KEY, "22000", "buy_secret_77", "dash-tok"):
        assert raw not in caplog.text


def test_money_switch_on_but_gate_failed_never_reaches_route(
    client: TestClient, adapter_dir: Path, safety: FakeSafety
) -> None:
    """켜 둬도 관문을 못 지나면 '직접 쓰기 가능'까지 가지 않는다."""
    _with_writes(adapter_dir, refund=REFUND_MAP)
    (adapter_dir / writes.SWITCH_FILE).write_text(
        json.dumps({"enabled": ["refund"]}), encoding="utf-8"
    )
    assert writes.route("refund") == "direct_off"  # execution_id 없이는 꺼짐
    assert writes.route("refund", "exe_refund_1") == writes.DIRECT_READY
    safety.record("refund")["decision"]["status"] = "held"
    assert send(client, "refund").status_code == 403
    assert_nothing_kept(adapter_dir)
