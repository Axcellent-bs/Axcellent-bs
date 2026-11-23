"""회사 대표 캐릭터 경로 (`/api/dashboard/marketing/brand-characters`, 11/23).

사람 토큰 필요(없으면 401), 권리 확인이 없으면 마케팅에 보내지 않고 422, 로그인 회사가 요청 meta에
실려 마케팅이 그 회사 기록만 본다. 제작 기록 파일 자리도 회사를 따른다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/brand-characters"
RID = "CR-0123456789ab"


class Fake:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"items": []}

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "marketing"
        self.calls.append(request)
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=self.output
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setattr(marketing_client, "SEND", f)
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def _auth(company: str = "catbooks", name: str = "태윤") -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name=name, role="admin", company_id=company, company_role="admin"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", URL), ("post", f"{URL}/{RID}"), ("delete", f"{URL}/{RID}")],
)
def test_needs_a_human_token(client: TestClient, fake: Fake, method: str, path: str) -> None:
    kw = {"json": {"rights_confirmed": True}} if method == "post" else {}
    client.headers.pop("authorization", None)  # conftest가 넣는 기본 사람 토큰을 지운다
    assert getattr(client, method)(path, **kw).status_code == 401
    assert fake.calls == []


def test_mark_without_rights_check_is_422_and_not_sent(client: TestClient, fake: Fake) -> None:
    for body in ({}, {"rights_confirmed": False}):
        res = client.post(f"{URL}/{RID}", json=body, headers=_auth())
        assert res.status_code == 422
        assert "권리" in res.json()["error"]["message"]
    assert fake.calls == []


def test_mark_sends_who_confirmed_from_the_token(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": RID, "marked": True, "dropped": None, "items": []}
    res = client.post(f"{URL}/{RID}", json={"rights_confirmed": True}, headers=_auth())
    assert res.status_code == 200
    sent = fake.calls[0]
    assert sent.skill == "brand_character_mark"
    assert sent.input == {"record_id": RID, "rights_confirmed": True, "who": "태윤"}


def test_marketing_refusal_maps_to_422(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, "rights_not_confirmed"
    fake.output = {"message": "확인해 주세요"}
    res = client.post(f"{URL}/{RID}", json={"rights_confirmed": True}, headers=_auth())
    assert res.status_code == 422


def test_unmark_and_not_found(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": RID, "marked": False, "dropped": None, "items": []}
    assert client.delete(f"{URL}/{RID}", headers=_auth()).status_code == 200
    assert fake.calls[0].skill == "brand_character_unmark"
    fake.status, fake.outcome = TaskStatus.FAILED, "not_found"
    assert client.delete(f"{URL}/{RID}", headers=_auth()).status_code == 404
    bad = client.post(f"{URL}/CR-x", json={"rights_confirmed": True}, headers=_auth())
    assert bad.status_code == 422


def test_login_company_goes_to_marketing(client: TestClient, fake: Fake) -> None:
    assert client.get(URL, headers=_auth("catbooks")).status_code == 200
    assert client.get(URL, headers=_auth("nubitour")).status_code == 200
    first, second = fake.calls
    assert first.skill == second.skill == "brand_characters_list"
    assert first.meta.tenant_id == "shop-demo"  # 캣북스는 지금 기본값 그대로
    assert second.meta.tenant_id == "nubitour"  # 다른 회사는 그 회사 기록만 본다


def test_creative_files_follow_the_company_folder(
    client: TestClient, fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """다른 회사 제작 기록 파일은 `companies/<회사>/creative/` 아래에서만 찾는다."""
    monkeypatch.setenv(marketing_client.MEDIA_DIR_ENV, str(tmp_path))
    data = b"\xff\xd8\xff" + b"0" * 20
    sha = hashlib.sha256(data).hexdigest()
    fake.output = {
        "record_id": RID,
        "created_at": "2026-11-23T00:00:00+00:00",
        "sheet": {},
        "render": {"sheet_image": {"status": "done", "asset": "sheet.jpg", "sha256": sha}},
    }
    own = tmp_path / "companies" / "nubitour" / "creative" / RID
    own.mkdir(parents=True)
    (own / "sheet.jpg").write_bytes(data)
    asset = f"/api/dashboard/marketing/creative-records/{RID}/asset/sheet_image"
    assert client.get(asset, headers=_auth("nubitour")).status_code == 200
    # 캣북스 자리(creative/)에는 그 파일이 없다
    assert client.get(asset, headers=_auth("catbooks")).status_code == 404
