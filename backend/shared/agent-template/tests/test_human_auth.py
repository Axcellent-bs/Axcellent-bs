"""사람 토큰 공용 검사: 세션 토큰(서명·만료·끊은 목록)과 옮기는 동안의 공용 토큰."""

import base64
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


def _token(now: int = 1_000_000, role: str = "operator") -> str:
    return ha.issue_session(account_id="acc-1", name="예진", role=role, now=now)[0]


def test_a_session_token_carries_name_role_and_twelve_hour_expiry() -> None:
    token, exp = ha.issue_session(account_id="acc-1", name="예진", role="admin", now=100)
    assert exp == 100 + 12 * 60 * 60
    human = ha.verify_human(f"Bearer {token}", now=200)
    assert (human.name, human.role, human.account_id, human.shared) == (
        "예진",
        "admin",
        "acc-1",
        False,
    )


def test_an_expired_token_is_401() -> None:
    token = _token(now=0)
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_session(token, now=ha.SESSION_TTL_SECONDS)
    assert (err.value.status_code, err.value.code) == (401, "SESSION_EXPIRED")


def test_a_forged_payload_or_signature_is_refused() -> None:
    prefix, body, sig = _token().split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["role"] = "operator"
    payload["name"] = "다른 사람"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    # 서명 끝 두 글자를 "AA"로 바꾸면 원래 끝이 "AA"일 때(약 4096분의 1) 서명이 그대로라 거절되지 않았다.
    # 첫 글자를 반드시 다른 글자로 바꿔 어떤 토큰에서도 서명이 달라지게 한다.
    tampered_sig = ("B" if sig[0] == "A" else "A") + sig[1:]
    for bad in (f"{prefix}.{forged}.{sig}", f"{prefix}.{body}.{tampered_sig}", f"{prefix}.{body}"):
        with pytest.raises(ha.HumanAuthError) as err:
            ha.verify_session(bad, now=1_000_001)
        assert err.value.code == "HUMAN_TOKEN_INVALID"


def test_a_token_signed_with_another_key_is_refused(monkeypatch) -> None:
    token = _token()
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, "y" * 40)
    with pytest.raises(ha.HumanAuthError):
        ha.verify_session(token, now=1_000_001)


def test_no_or_short_secret_refuses_every_session_token(monkeypatch) -> None:
    token = _token()
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, "short")
    assert ha.sessions_enabled() is False
    with pytest.raises(ha.HumanAuthError):
        ha.verify_session(token, now=1_000_001)
    with pytest.raises(RuntimeError):
        _token()


def test_logout_revokes_until_expiry_through_the_shared_file(tmp_path) -> None:
    token = _token()
    human = ha.verify_session(token, now=1_000_001)
    ha.revoke_session(human, now=1_000_001)
    ha.reset_revoked_for_tests()  # 다른 서비스(메모리 없음)도 파일로 안다
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_session(token, now=1_000_002)
    assert err.value.code == "SESSION_REVOKED"
    assert human.session_id in json.loads((tmp_path / "revoked.json").read_text())


def test_shared_tokens_still_pass_until_switched_off(monkeypatch) -> None:
    shared = ha.parse_shared_tokens("공용:shared-token,taeyoon:t2")
    human = ha.verify_human("Bearer shared-token", shared_tokens=shared)
    assert (human.name, human.role, human.shared) == ("공용", "shared", True)
    monkeypatch.setenv(ha.ALLOW_SHARED_ENV, "0")
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_human("Bearer shared-token", shared_tokens=shared)
    assert err.value.code == "HUMAN_TOKEN_INVALID"


@pytest.mark.parametrize(
    ("header", "status", "code"),
    [
        (None, 401, "AUTH_REQUIRED"),
        ("Basic abc", 401, "AUTH_REQUIRED"),
        ("Bearer mock:orchestrator", 403, "HUMAN_TOKEN_REQUIRED"),
        ("Bearer unknown", 403, "HUMAN_TOKEN_INVALID"),
    ],
)
def test_missing_agent_and_unknown_tokens_are_refused(header, status, code) -> None:
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_human(header, shared_tokens={"known": "공용"})
    assert (err.value.status_code, err.value.code) == (status, code)


# ---- find_human: 서비스마다 가진 사람 검사가 쓰는 함수 ----


def test_find_human_reads_a_session_token_as_the_account() -> None:
    human = ha.find_human(_token(), now=1_000_001)
    assert human is not None
    assert (human.name, human.role, human.shared) == ("예진", "operator", False)


def test_find_human_raises_for_a_bad_or_revoked_session() -> None:
    token = _token()
    prefix, body, sig = token.split(".")
    with pytest.raises(ha.HumanAuthError):
        ha.find_human(f"{prefix}.{body}.{'A' * len(sig)}", now=1_000_001)
    ha.revoke_session(ha.verify_session(token, now=1_000_001), now=1_000_001)
    with pytest.raises(ha.HumanAuthError) as err:
        ha.find_human(token, now=1_000_002)
    assert err.value.code == "SESSION_REVOKED"


def test_find_human_keeps_shared_tokens_until_switched_off(monkeypatch) -> None:
    shared = {"s3cret": "공용"}
    human = ha.find_human("s3cret", shared_tokens=shared)
    assert human is not None and (human.name, human.shared) == ("공용", True)
    monkeypatch.setenv(ha.ALLOW_SHARED_ENV, "0")
    assert ha.find_human("s3cret", shared_tokens=shared) is None


def test_find_human_takes_mock_names_only_under_the_mock_switch() -> None:
    assert ha.find_human("human:taeyoon") is None
    mocked = ha.find_human("human:taeyoon", mock=True)
    assert mocked is not None and mocked.name == "taeyoon"
    assert ha.find_human("human:", mock=True) is None


def test_find_human_returns_none_for_agent_and_unknown_tokens() -> None:
    assert ha.find_human("mock:marketing", shared_tokens={"mock:marketing": "x"}, mock=True) is None
    assert ha.find_human("nobody", shared_tokens={"s3cret": "공용"}) is None


# ---- 계정 세대 번호: 비밀번호를 바꾸면 그 계정의 기존 세션을 모두 끊는다 ----


def test_ending_account_sessions_refuses_older_tokens_through_the_shared_file(tmp_path) -> None:
    old = _token()
    other = ha.issue_session(account_id="acc-2", name="태윤", role="member", now=1_000_000)[0]
    assert ha.end_account_sessions("acc-1") == 1
    ha.reset_revoked_for_tests()  # 다른 서비스(메모리 없음)도 파일로 안다
    with pytest.raises(ha.HumanAuthError) as err:
        ha.verify_session(old, now=1_000_001)
    assert (err.value.status_code, err.value.code) == (401, "SESSION_ENDED")
    with pytest.raises(ha.HumanAuthError):
        ha.find_human(old, now=1_000_001)
    # 다른 계정의 세션과 새로 만든 세션은 그대로 통과
    assert ha.verify_session(other, now=1_000_001).name == "태윤"
    assert ha.verify_session(_token(), now=1_000_001).name == "예진"
    data = json.loads((tmp_path / ha.GENERATIONS_FILE).read_text(encoding="utf-8"))
    assert data == {"acc-1": 1}


def test_tokens_without_a_generation_count_as_zero() -> None:
    legacy = ha.issue_session(account_id="acc-1", name="예진", role="member", now=1_000_000, gen=0)
    assert ha.verify_session(legacy[0], now=1_000_001).account_id == "acc-1"
    ha.end_account_sessions("acc-1")
    with pytest.raises(ha.HumanAuthError):
        ha.verify_session(legacy[0], now=1_000_001)


def test_ending_sessions_without_a_file_still_works_in_this_process(monkeypatch) -> None:
    monkeypatch.setenv(ha.REVOKED_FILE_ENV, "")
    monkeypatch.setattr(ha.company_connection, "connection_file", lambda: None)
    assert ha.generations_file() is None
    old = _token()
    ha.end_account_sessions("acc-1")
    with pytest.raises(ha.HumanAuthError):
        ha.verify_session(old, now=1_000_001)
