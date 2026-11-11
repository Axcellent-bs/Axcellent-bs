"""내 문의 목록·상태 읽기: 고객이 받은 번호는 판매 서버 접수 번호(seq)다.

고객지원이 읽어 간 문의는 그 번호로 고객지원 문의에 이어서(shop-api → 고객지원 list_inquiries·
get_inquiry) 본인 범위·고객용 모양으로 보고, 아직 읽어 가지 않은 문의는 접수한 내용만 보여 준다.
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_shop_api import inbox, support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

INTERNAL = "내부 초안·분류·상담원 메모"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


def _item(id_: int, status: str, rated: bool, **extra) -> dict:  # noqa: ANN003
    return {
        "id": id_,
        "subject": f"문의 {id_}",
        "status": status,
        "order_id": "ord_0001",
        "updated_at": "2026-10-30T00:00:00Z",
        "rated": rated,
        "preview": "미리보기",
    } | extra


class FakeSupport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.output: dict = {}
        self.outcome: str | None = None
        self.status = TaskStatus.COMPLETED

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=self.output
        )


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> FakeSupport:
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def seed(
    db: Db, buyer: str = "buy_0001", *, support_id: int | None = None, text: str = "원문"
) -> int:
    """접수를 하나 넣고, 고객지원이 읽어 갔으면 그 번호를 잇는다 (고객 번호 = 접수 번호)."""
    saved = inbox.add(
        db,
        digest=f"{buyer}-{text}-{support_id}",
        context_id="ctx_shop_aaaaaaaaaaaa",
        buyer_id=buyer,
        customer_name="김하늘",
        order_id="ord_0001",
        subject=f"접수 {text}",
        message=text,
        images=None,
    )
    if support_id is not None:
        inbox.mark_synced(db, saved["seq"], support_id)
    return saved["seq"]


def test_목록은_접수_번호로_보여_주고_읽어_간_문의는_고객지원_값을_고객용으로_좁혀_쓴다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    seqs = [seed(db, support_id=s, text=f"글{s}") for s in (30, 20, 10, 40)]
    support.output = {
        "items": [
            _item(30, "resolved", False, analysis=INTERNAL, customer_name="김하늘"),
            _item(20, "resolved", True),
            _item(10, "open", False),
            _item(40, "escalated", False),
        ]
    }
    response = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"})
    assert response.status_code == 200
    body = response.json()
    # 고객이 보는 번호는 접수 번호(최근 순)이고 고객지원 번호(30·20·10·40)가 아니다
    assert [(i["id"], i["can_rate"]) for i in body["data"]] == [
        (seqs[3], False),  # 에스컬레이션
        (seqs[2], False),  # 아직 해결되지 않음
        (seqs[1], False),  # 이미 평가함
        (seqs[0], True),  # 해결됐고 아직 평가하지 않음
    ]
    assert body["meta"]["total"] == 4
    assert INTERNAL not in response.text and "김하늘" not in response.text  # 모르는 키는 버린다
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "list_inquiries")
    assert request.input == {"user_id": 1} and request.meta.token == "mock:shop-api"
    assert request.meta.idempotency_key is None  # 읽기다


def test_고객지원이_아직_읽지_않은_문의도_목록에_보이고_고객지원은_부르지_않는다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    seq = seed(db, text="아직 처리 전인 문의 내용입니다")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    (row,) = body["data"]
    assert row["id"] == seq and row["status"] == "open" and row["can_rate"] is False
    assert row["rated"] is False and row["preview"].startswith("아직 처리 전인")
    assert support.calls == []  # 읽어 간 문의가 없으면 고객지원에 묻지 않는다


def test_다른_구매자의_접수는_목록에_없다(client: TestClient, db: Db, support: FakeSupport) -> None:
    seed(db, "buy_0002", text="남의 문의")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert body["data"] == [] and "남의 문의" not in str(body)


def test_목록은_limit만큼만_돌려주고_전체_수는_meta에_둔다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    for n in range(5):
        seed(db, text=f"글{n}")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 2}).json()
    assert [i["id"] for i in body["data"]] == [5, 4] and body["meta"]["total"] == 5
    assert (
        client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 0}).status_code
        == 422
    )


def test_문의가_없으면_빈_목록(client: TestClient, support: FakeSupport) -> None:
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert body["data"] == [] and body["meta"]["total"] == 0


def test_구매자_ID가_없거나_틀리면_거절하고_고객지원을_부르지_않는다(
    client: TestClient, support: FakeSupport
) -> None:
    assert client.get("/api/shop/inquiries").status_code == 422
    bad = client.get("/api/shop/inquiries", params={"buyer_id": "buy_1"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "INVALID_BUYER_ID")
    assert client.get("/api/shop/inquiries/5", params={"buyer_id": "7"}).status_code == 400
    assert support.calls == []


def test_상세는_접수_번호를_고객지원_번호로_바꿔_부르고_메시지_글만_보여_준다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    for n in range(3):  # 접수 번호가 고객지원 번호와 겹치지 않게 앞에 몇 건을 둔다
        seed(db, "buy_0002", text=f"남{n}")
    seq = seed(db, support_id=5)
    support.output = _item(5, "resolved", False, analysis=INTERNAL) | {
        "messages": [
            {
                "id": 1,
                "role": "customer",
                "text": "환불해 주세요",
                "created_at": "t1",
                "analysis": INTERNAL,
            },
            {"id": 2, "role": "agent", "text": "처리했습니다", "created_at": "t2"},
        ]
    }
    response = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["can_rate"] is True and data["id"] == seq != 5  # 고객이 받은 번호 그대로
    assert [set(m) for m in data["messages"]] == [{"id", "role", "text", "created_at"}] * 2
    assert INTERNAL not in response.text
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "get_inquiry")
    assert request.input == {"inquiry_id": 5, "user_id": 1}  # 고객지원 번호로 물었다


def test_읽어_가지_않은_문의_상세는_접수한_글만_보여_주고_고객지원을_부르지_않는다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    seq = seed(db, text="아직 처리 전")
    data = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"}).json()["data"]
    assert data["id"] == seq and data["status"] == "open" and data["can_rate"] is False
    assert [(m["role"], m["text"]) for m in data["messages"]] == [("customer", "아직 처리 전")]
    assert support.calls == []


def test_남의_문의와_없는_문의는_같은_404이고_고객지원을_부르지_않는다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    seq = seed(db, support_id=5)
    other = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0002"})
    missing = client.get("/api/shop/inquiries/99", params={"buyer_id": "buy_0001"})
    assert (other.status_code, other.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert other.json() == missing.json()
    assert support.calls == []


def test_문의_번호는_1_이상_정수(client: TestClient, support: FakeSupport) -> None:
    assert client.get("/api/shop/inquiries/0", params={"buyer_id": "buy_0001"}).status_code == 422
    assert client.get("/api/shop/inquiries/abc", params={"buyer_id": "buy_0001"}).status_code == 422
    assert support.calls == []


def test_문의가_아닌_응답은_성공으로_내보내지_않는다(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    seq = seed(db, support_id=5)
    support.output = {"message": "이상한 응답"}
    response = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"})
    assert (response.status_code, response.json()["error"]["code"]) == (502, "UPSTREAM_ERROR")


@pytest.mark.parametrize(
    ("status", "http", "code"),
    [
        (TaskStatus.CANCELED, 504, "UPSTREAM_TIMEOUT"),
        (TaskStatus.FAILED, 502, "UPSTREAM_ERROR"),
    ],
)
def test_고객지원_오류는_상태와_코드로_바뀐다(
    client: TestClient, db: Db, support: FakeSupport, status: TaskStatus, http: int, code: str
) -> None:
    seed(db, support_id=5)  # 읽어 간 문의가 있어야 고객지원에 묻는다
    support.status = status
    response = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"})
    assert (response.status_code, response.json()["error"]["code"]) == (http, code)


def test_a_read_inquiry_support_no_longer_has_is_not_listed_again(
    client: TestClient, db: Db, support: FakeSupport
) -> None:
    """개인정보로 지웠거나 고객지원이 다시 시작해 찾을 수 없는 문의는 원문을 다시 안 보여 준다."""
    kept = seed(db, support_id=5, text="남아 있는 문의")
    seed(db, support_id=6, text="지워진 문의 원문")
    support.output = {"items": [_item(5, "open", False)]}  # 6은 고객지원 목록에 없다
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert [i["id"] for i in body["data"]] == [kept] and body["meta"]["total"] == 1
    assert "지워진 문의 원문" not in str(body)


def test_a_synced_inquiry_no_longer_keeps_the_text_copy_in_the_shop(db: Db) -> None:
    seq = seed(db, support_id=5, text="읽어 간 뒤에는 지운다")
    with db.tx() as conn:
        row = conn.execute("SELECT message FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
    assert row["message"] == ""


def test_an_inquiry_set_aside_after_repeated_failures_tells_the_customer(
    client: TestClient, db: Db, support: FakeSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INBOX_MAX_ATTEMPTS", "1")
    seq = seed(db, text="끝내 처리되지 않는 문의")
    inbox.report_failure(db, seq, "INGEST_FAILED")
    (row,) = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()["data"]
    assert row["intake_failed"] is True and "고객센터" in row["preview"]
    assert "끝내 처리되지 않는" not in row["preview"]
    detail = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"}).json()
    assert "고객센터" in detail["data"]["notice"]
