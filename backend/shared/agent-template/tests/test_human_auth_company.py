"""세션 토큰의 회사 (여러 회사 설계 1-3).

토큰 내용에 회사 ID `cid`·회사 역할 `crole`을 더한다. 형식은 `axs1` 그대로다.
1단계에서는 회사가 캣북스 하나라, 회사가 없는 옛 토큰도 받고 캣북스로 본다(거절은 4단계).
"""

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from ax_agent_template import human_auth as ha

SECRET = "x" * 40  # 테스트용 자리값 (실제 키 아님)


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(ha.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(ha.ALLOW_SHARED_ENV, raising=False)
    ha.reset_revoked_for_tests()


def _payload(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def _signed(payload: dict) -> str:
    """이 기능 전에 만든 토큰처럼, 주어진 내용 그대로 서명한다."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = hmac.new(SECRET.encode(), f"axs1.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"axs1.{body}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')}"


def test_a_token_with_a_company_carries_cid_and_crole() -> None:
    token, _ = ha.issue_session(
        account_id="acc-1",
        name="예진",
        role="admin",
        company_id="catbooks",
        company_role="admin",
        now=100,
    )
    assert token.startswith("axs1.")
    payload = _payload(token)
    assert (payload["cid"], payload["crole"]) == ("catbooks", "admin")
    human = ha.verify_session(token, now=200)
    assert (human.company_id, human.company_role) == ("catbooks", "admin")
    assert (human.name, human.role, human.account_id) == ("예진", "admin", "acc-1")


def test_the_alias_is_signed_as_the_canonical_company_id() -> None:
    token, _ = ha.issue_session(
        account_id="acc-1",
        name="예진",
        role="member",
        company_id="shop-demo",
        company_role="member",
    )
    assert _payload(token)["cid"] == "catbooks"
    assert ha.verify_session(token).company_id == "catbooks"


def test_without_a_company_the_token_has_no_cid_like_before() -> None:
    token, _ = ha.issue_session(account_id="acc-1", name="운영", role="operator", now=100)
    payload = _payload(token)
    assert "cid" not in payload and "crole" not in payload


@pytest.mark.parametrize(
    ("role", "company_role"), [("admin", "admin"), ("member", "member"), ("operator", None)]
)
def test_an_old_token_without_cid_is_read_as_catbooks(role: str, company_role: str | None) -> None:
    old = _signed(
        {"sub": "acc-1", "name": "예진", "role": role, "iat": 100, "exp": 10_000, "jti": "j1"}
    )
    human = ha.verify_session(old, now=200)
    assert human.company_id == "catbooks"
    # 회사 역할이 없으면 계정 역할을 캣북스의 그 역할로 읽는다. 운영자는 회사 역할이 없다
    assert human.company_role == company_role
    assert human.role == role


def test_an_old_cid_alias_is_read_as_the_canonical_id() -> None:
    token = _signed(
        {
            "sub": "acc-1",
            "name": "예진",
            "role": "member",
            "iat": 100,
            "exp": 10_000,
            "jti": "j2",
            "cid": "shop-demo",
            "crole": "member",
        }
    )
    assert ha.verify_session(token, now=200).company_id == "catbooks"


@pytest.mark.parametrize(
    "extra",
    [
        {"cid": "Bad Id"},
        {"cid": 7},
        {"cid": "catbooks", "crole": "operator"},
        {"cid": "catbooks", "crole": 1},
    ],
)
def test_a_signed_token_with_a_broken_company_is_refused(extra: dict) -> None:
    token = _signed(
        {"sub": "acc-1", "name": "예진", "role": "admin", "iat": 100, "exp": 10_000, "jti": "j3"}
        | extra
    )
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_session(token, now=200)
    assert err.value.code == "HUMAN_TOKEN_INVALID"


def test_issue_refuses_a_broken_company() -> None:
    for kwargs in (
        {"company_id": "Bad Id", "company_role": "admin"},
        {"company_id": "catbooks", "company_role": "operator"},
        {"company_role": "admin"},  # 회사 없이 회사 역할만은 안 된다
    ):
        with pytest.raises(ValueError):
            ha.issue_session(account_id="acc-1", name="예진", role="admin", **kwargs)


def test_shared_tokens_have_no_company() -> None:
    human = ha.find_human("s3cret", shared_tokens={"s3cret": "공용"})
    assert human is not None and (human.company_id, human.company_role) == (None, None)
