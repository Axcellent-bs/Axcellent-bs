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


def test_executions_are_kept_per_session_company(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실행 기록(AI 대화방·안전 화면이 3초마다 읽음)도 회사별(11/25 회사 분리).

    누비투어 세션은 누비투어 실행만, 캣북스 세션은 캣북스 실행만 목록·상세에서 본다."""
    cat = coupon(1, 1000, key="cat-1").model_dump(mode="json")
    nubi_req = coupon(1, 1000, key="nubi-1").model_copy(update={"tenant_id": "nubi"})
    cat_id = client.post("/api/safety/executions", json=cat, headers=MARKETING).json()["data"][
        "execution_id"
    ]
    nubi_id = client.post(
        "/api/safety/executions", json=nubi_req.model_dump(mode="json"), headers=MARKETING
    ).json()["data"]["execution_id"]
    nubi = human_auth.issue_session(
        account_id="acc-n", name="누비관리", role="admin", company_id="nubi", company_role="admin"
    )[0]

    def ids(headers: dict[str, str]) -> set[str]:
        rows = client.get("/api/safety/executions", headers=headers).json()["data"]
        return {r["execution_id"] for r in rows}

    assert ids(_bearer(nubi)) == {nubi_id}
    assert cat_id in ids(_bearer(session))  # 캣북스 세션은 캣북스 기록을 본다
    assert client.get(f"/api/safety/executions/{cat_id}", headers=_bearer(nubi)).status_code == 404
    assert client.get(f"/api/safety/executions/{nubi_id}", headers=_bearer(nubi)).status_code == 200
    no_company = human_auth.issue_session(
        account_id="acc-x", name="끊긴회원", role="member", no_company=True
    )[0]
    assert ids(_bearer(no_company)) == set()


def test_shared_token_with_session_header_reads_only_that_company(
    client: Any, coupon: Any, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 출처 nginx는 읽기의 Authorization을 공용 토큰으로 바꿔 붙인다(11/25 실서버).

    화면이 같이 싣는 X-AX-Session으로 회사를 고른다: 누비투어 세션이면 누비투어 기록만.
    위조된 머리는 무시(예전처럼 전부)."""
    cat = coupon(1, 1000, key="cat-h").model_dump(mode="json")
    nubi_req = coupon(1, 1000, key="nubi-h").model_copy(update={"tenant_id": "nubi"})
    cat_id = client.post("/api/safety/executions", json=cat, headers=MARKETING).json()["data"][
        "execution_id"
    ]
    nubi_id = client.post(
        "/api/safety/executions", json=nubi_req.model_dump(mode="json"), headers=MARKETING
    ).json()["data"]["execution_id"]
    nubi = human_auth.issue_session(
        account_id="acc-n", name="누비관리", role="admin", company_id="nubi", company_role="admin"
    )[0]

    def ids(headers: dict[str, str]) -> set[str]:
        rows = client.get("/api/safety/executions", headers=headers).json()["data"]
        return {r["execution_id"] for r in rows}

    assert ids({"X-AX-Session": nubi}) == {nubi_id}  # 공용 토큰 주입 = 사람 토큰 아님과 같은 경우
    assert ids({"X-AX-Session": _forged(nubi)}) >= {
        cat_id,
        nubi_id,
    }  # 위조: 회사를 안 고름(예전 그대로)
    assert ids({}) >= {cat_id, nubi_id}  # 머리 없음: 예전 그대로
