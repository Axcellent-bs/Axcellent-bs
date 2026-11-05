"""고객지원 AI 직접 대화 API (`/api/dashboard/support-chat`): 보내기·읽기·닫기와 보는 사람 판정."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import support_chat, support_client
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.main import app

BASE = "/api/dashboard/support-chat"
HUMAN = {"Authorization": "Bearer tok-gongyong"}  # 이름: 공용
STAFF = {"Authorization": "Bearer tok-damdang"}  # 이름: 담당자


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.outcomes: dict[str, str] = {}

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "support"
        self.sent.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome=self.outcomes.get(req.skill),
            output=self.outputs.get(req.skill, {}),
        )

    def last(self, skill: str) -> AgentRequest:
        return next(r for r in reversed(self.sent) if r.skill == skill)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:tok-gongyong,담당자:tok-damdang")
    monkeypatch.setenv(support_chat.STAFF_ENV, "담당자")
    monkeypatch.delenv("AX_ORG_CONFIG", raising=False)
    fake = Fake()
    fake.outputs = {
        "inquiry_overview": {
            "kpis": {"conversations": 8, "by_status": {"open": 6}, "draft_adoption_rate": None},
            "waiting": [
                {
                    "id": 1,
                    "customer_name": "홍길동",
                    "subject": "010-1234-5678 환불해 주세요",
                    "preview": "원문 미리보기",
                    "category": "refund",
                    "urgency": "high",
                    "waiting_minutes": 25,
                },
                {"id": 2, "category": "shipping", "urgency": "low", "waiting_minutes": 5},
            ],
        },
        "claim_stats": {"total": 3, "by_status": {"auto_rejected": 2}, "rule_hits": {"X": 2}},
        "feedback_overview": {"total": 4, "average_rating": 4.5, "note": "후기 원문 문장"},
        "chat_send": {"id": "chat-1", "status": "answering", "who": "공용", "messages": []},
        "chat_list": {"items": [{"id": "chat-1"}], "total": 1},
        "chat_get": {"id": "chat-1", "status": "talking"},
        "chat_close": {"id": "chat-1", "status": "closed", "summary": "요약"},
    }
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------- 보내기
def test_send_fills_who_from_the_token_and_passes_the_text_as_untrusted(
    client: TestClient, fake: Fake
) -> None:
    res = client.post(BASE, json={"message": "답변 대기가 몇 건이에요?"}, headers=HUMAN)
    assert res.status_code == 200 and res.json()["data"]["status"] == "answering"
    sent = fake.last("chat_send")
    assert sent.input["who"] == "공용" and sent.input["thread_id"] is None
    assert sent.untrusted == {"message": "답변 대기가 몇 건이에요?"}
    assert "message" not in sent.input  # 사람의 말은 입력(지시)에 섞이지 않는다


def test_a_who_in_the_body_is_not_trusted(client: TestClient, fake: Fake) -> None:
    res = client.post(BASE, json={"message": "안녕", "who": "관리자"}, headers=HUMAN)
    assert res.status_code == 200  # 모르는 필드는 무시하고
    assert fake.last("chat_send").input["who"] == "공용"  # 말한 사람은 토큰에서만 채운다


def test_send_requires_a_human_token(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization")  # conftest가 붙인 기본 사람 토큰을 지운다
    assert client.post(BASE, json={"message": "안녕"}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post(BASE, json={"message": "안녕"}, headers=bad).status_code == 403
    assert not fake.sent


def test_send_validates_the_message(client: TestClient, fake: Fake) -> None:
    assert client.post(BASE, json={"message": ""}, headers=HUMAN).status_code == 422
    assert client.post(BASE, json={"message": "가" * 4001}, headers=HUMAN).status_code == 422
    assert client.post(BASE, json={}, headers=HUMAN).status_code == 422
    assert not fake.sent


def test_follow_up_carries_the_thread_id(client: TestClient, fake: Fake) -> None:
    client.post(BASE, json={"message": "또", "thread_id": "chat-1"}, headers=HUMAN)
    assert fake.last("chat_send").input["thread_id"] == "chat-1"


def test_the_context_has_numbers_only(client: TestClient, fake: Fake) -> None:
    client.post(BASE, json={"message": "현황은?"}, headers=HUMAN)
    context = fake.last("chat_send").input["context"]
    assert context["inquiries"]["waiting"] == {
        "count": 2,
        "by_urgency": {"high": 1, "low": 1},
        "by_category": {"refund": 1, "shipping": 1},
        "longest_minutes": 25,
    }
    assert context["inquiries"]["kpis"] == {"conversations": 8, "by_status": {"open": 6}}
    assert context["claims"]["stats"]["total"] == 3
    assert context["feedback"]["overview"] == {"total": 4, "average_rating": 4.5}
    flat = str(context)
    for secret in ("홍길동", "010-1234-5678", "원문 미리보기", "후기 원문 문장"):
        assert secret not in flat  # 이름·제목·미리보기·원문은 대화로 가지 않는다


def test_an_unreadable_source_is_unavailable_not_invented(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = support_client.call

    async def flaky(skill: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if skill == "claim_stats":
            raise ApiError(502, "UPSTREAM_ERROR", "down")
        return await real(skill, *args, **kwargs)

    monkeypatch.setattr(support_client, "call", flaky)
    client.post(BASE, json={"message": "현황은?"}, headers=HUMAN)
    context = fake.last("chat_send").input["context"]
    assert context["claims"] == {"available": False, "note": "확인할 수 없음"}
    assert context["inquiries"]["available"] is True


# ---------------------------------------------------------------- 읽기·닫기
def test_reads_pass_the_viewer_name_and_role(client: TestClient, fake: Fake) -> None:
    client.get(BASE, headers=HUMAN)
    assert fake.last("chat_list").input == {"viewer": "공용", "role": "member"}
    client.get(BASE + "/chat-1", headers=STAFF)
    assert fake.last("chat_get").input == {
        "thread_id": "chat-1",
        "viewer": "담당자",
        "role": "staff",
    }


def test_reads_need_a_human_token_too(client: TestClient, fake: Fake) -> None:
    """인사 AI 직접 대화와 같다: 읽기에도 사람 토큰이 필요하다 (없으면 401, 모르면 403)."""
    client.headers.pop("authorization")  # conftest가 붙인 기본 사람 토큰을 지운다
    assert client.get(BASE).status_code == 401
    assert client.get(BASE + "/chat-1").status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.get(BASE, headers=wrong).status_code == 403
    assert client.get(BASE + "/chat-1", headers=wrong).status_code == 403
    assert not fake.sent


def test_the_response_data_is_the_thread_list(client: TestClient, fake: Fake) -> None:
    body = client.get(BASE, headers=HUMAN).json()
    assert body["data"] == [{"id": "chat-1"}] and body["meta"]["total"] == 1


def test_missing_thread_is_404(client: TestClient, fake: Fake) -> None:
    fake.outcomes["chat_get"] = "not_found"
    fake.outputs["chat_get"] = {"message": "대화 chat-9를 찾을 수 없다"}
    assert client.get(BASE + "/chat-9", headers=HUMAN).status_code == 404


def test_writing_into_someone_elses_thread_is_403(client: TestClient, fake: Fake) -> None:
    fake.outcomes["chat_send"] = "forbidden"
    fake.outputs["chat_send"] = {"message": "다른 사람의 대화에는 쓸 수 없다"}
    res = client.post(BASE, json={"message": "끼어들기", "thread_id": "chat-1"}, headers=HUMAN)
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"
    fake.outcomes["chat_close"] = "forbidden"
    assert client.post(BASE + "/chat-1/close", headers=HUMAN).status_code == 403


def test_busy_thread_is_409(client: TestClient, fake: Fake) -> None:
    fake.outcomes["chat_send"] = "invalid_state"
    fake.outputs["chat_send"] = {"message": "답을 만드는 중이다"}
    res = client.post(BASE, json={"message": "또", "thread_id": "chat-1"}, headers=HUMAN)
    assert res.status_code == 409


def test_close_uses_the_token_name_and_role(client: TestClient, fake: Fake) -> None:
    res = client.post(BASE + "/chat-1/close", headers=STAFF)
    assert res.status_code == 200 and res.json()["data"]["status"] == "closed"
    assert fake.last("chat_close").input == {
        "thread_id": "chat-1",
        "viewer": "담당자",
        "role": "staff",
    }
    client.headers.pop("authorization")
    assert client.post(BASE + "/chat-1/close").status_code == 401


# ---------------------------------------------------------------- 보는 사람
def test_viewer_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:tok-a,담당자:tok-b")
    monkeypatch.setenv(support_chat.STAFF_ENV, " 담당자 , ")
    monkeypatch.delenv("AX_ORG_CONFIG", raising=False)
    assert support_chat.viewer_of("Bearer tok-a") == ("공용", "member")
    assert support_chat.viewer_of("Bearer tok-b") == ("담당자", "staff")
    with pytest.raises(ApiError) as missing:
        support_chat.viewer_of(None)
    assert missing.value.status_code == 401
    for bad in ("Bearer unknown", "Basic tok-b"):
        with pytest.raises(ApiError) as refused:
            support_chat.viewer_of(bad)
        assert refused.value.status_code in (401, 403)


def test_the_admin_agent_token_is_an_admin_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:tok-a")
    monkeypatch.setattr(
        support_chat, "verify_token", lambda t: "admin" if t == "tok-admin" else "x"
    )
    assert support_chat.viewer_of("Bearer tok-admin") == ("admin", "admin")
    monkeypatch.setattr(
        support_chat, "verify_token", lambda t: (_ for _ in ()).throw(PermissionError("no"))
    )
    with pytest.raises(ApiError) as refused:
        support_chat.viewer_of("Bearer tok-other")
    assert refused.value.status_code == 403


def test_staff_also_come_from_the_org_chart(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    org = tmp_path / "org.yaml"
    org.write_text(
        "assignments:\n"
        "  inquiry: {unit: 고객지원, assignee: 조직도담당}\n"
        "  planning: {unit: 기획팀, assignee: 기획담당}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AX_ORG_CONFIG", str(org))
    monkeypatch.delenv(support_chat.STAFF_ENV, raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "조직도담당:tok-c,기획담당:tok-d")
    assert support_chat.staff_names() == {"조직도담당"}  # 다른 부서 담당자는 아니다
    assert support_chat.viewer_of("Bearer tok-c") == ("조직도담당", "staff")
    assert support_chat.viewer_of("Bearer tok-d") == ("기획담당", "member")


def test_mock_mode_is_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    client = TestClient(app)
    assert client.get(BASE).status_code == 501
    headers = {"Authorization": "Bearer human:a"}
    assert client.post(BASE, json={"message": "안녕"}, headers=headers).status_code == 501
