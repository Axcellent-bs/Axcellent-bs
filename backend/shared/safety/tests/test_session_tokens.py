"""로그인 세션 토큰(`axs1.`)으로 보류 결정: 통과·위조·로그아웃·공용 토큰·기록 이름."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import human_auth

SECRET = "s" * 40  # 테스트용 자리값 (실제 키 아님)
MARKETING = {"Authorization": "Bearer mock:marketing"}


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    human_auth.reset_revoked_for_tests()
    return human_auth.issue_session(account_id="acc-1", name="예진", role="admin")[0]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _forged(token: str) -> str:
    prefix, body, sig = token.split(".")
    return f"{prefix}.{body}.{'A' * len(sig)}"


def _held_url(client: Any, coupon: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    body = coupon(20, 20_000, purpose_code="campaign", key="big").model_dump(mode="json")
    data = client.post("/api/safety/executions", json=body, headers=MARKETING).json()["data"]
    assert data["status"] == "held"
    # 결정은 실제 모드로 한다 (목 스위치를 끄면 `human:<이름>`은 안 된다)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK")
    return f"/api/safety/reviews/{data['review_id']}/decision"


def test_a_session_token_decides_and_records_the_account_name(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _held_url(client, coupon, monkeypatch)
    ok = client.post(url, json={"decision": "approved"}, headers=_bearer(session))
    assert ok.status_code == 200
    assert ok.json()["data"]["decided_by"] == "예진"


def test_a_forged_session_token_is_refused(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _held_url(client, coupon, monkeypatch)
    bad = client.post(url, json={"decision": "approved"}, headers=_bearer(_forged(session)))
    assert bad.status_code == 403
    assert client.get(url.removesuffix("/decision")).json()["data"]["status"] == "pending"


def test_a_logged_out_session_token_is_refused(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _held_url(client, coupon, monkeypatch)
    human_auth.revoke_session(human_auth.verify_session(session))
    bad = client.post(url, json={"decision": "approved"}, headers=_bearer(session))
    assert bad.status_code == 403
    assert "로그아웃" in bad.json()["error"]["message"]


def test_shared_token_still_works_with_its_old_name(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-secret")
    url = _held_url(client, coupon, monkeypatch)
    ok = client.post(url, json={"decision": "approved"}, headers=_bearer("shared-secret"))
    assert ok.json()["data"]["decided_by"] == "공용"


def test_shared_token_is_refused_once_switched_off(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-secret")
    monkeypatch.setenv(human_auth.ALLOW_SHARED_ENV, "0")
    url = _held_url(client, coupon, monkeypatch)
    bad = client.post(url, json={"decision": "approved"}, headers=_bearer("shared-secret"))
    assert bad.status_code == 403


def test_pending_approvals_are_kept_per_session_company(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """격리 시험 5번: 다른 회사 세션은 캣북스 승인 대기를 목록·상세·결정 어디서도 못 본다(11/24).

    캣북스 세션·공용 토큰은 예전 그대로 본다."""
    url = _held_url(client, coupon, monkeypatch)  # 캣북스(기본 회사) 건
    review_url = url.removesuffix("/decision")
    review_id = review_url.rsplit("/", 1)[1]
    nubi = human_auth.issue_session(
        account_id="acc-n", name="누비관리", role="admin", company_id="nubi", company_role="admin"
    )[0]

    def ids(headers: dict[str, str]) -> list[str]:
        rows = client.get("/api/safety/reviews?status=pending", headers=headers).json()["data"]
        return [r["review_id"] for r in rows]

    assert review_id not in ids(_bearer(nubi))
    assert client.get(review_url, headers=_bearer(nubi)).status_code == 404
    refused = client.post(url, json={"decision": "approved"}, headers=_bearer(nubi))
    assert refused.status_code == 404
    assert client.get(review_url).json()["data"]["status"] == "pending"  # 그대로
    assert review_id in ids(_bearer(session))  # 대조: 캣북스 세션은 본다
    no_company = human_auth.issue_session(
        account_id="acc-x", name="끊긴회원", role="member", no_company=True
    )[0]
    assert ids(_bearer(no_company)) == []  # 소속 없는 계정은 아무것도 못 본다
