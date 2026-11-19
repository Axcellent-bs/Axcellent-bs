"""비밀 저장소: 암호화·tenant 분리·시작 거부·캐시·이전 도구·값 유출 없음.

값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from ax_secret_store import errors, migrate, resolver
from ax_secret_store.crypto import Keyring, generate_master_key
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.service import SecretService

DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for var in (
        "AX_SECRET_STORE",
        "AX_SECRET_MASTER_KEY",
        "AX_SECRET_MASTER_KEY_ID",
        "AX_SECRET_MASTER_KEYS_OLD",
        "AX_SECRET_DEFAULT_TENANT",
        "AX_SECRET_MIGRATE_NAMES",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    resolver.reset_for_tests()
    yield
    resolver.reset_for_tests()


def db_mode(monkeypatch: pytest.MonkeyPatch, key: str | None = None) -> str:
    key = key or generate_master_key()
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", key)
    return key


def put(svc: SecretService, tenant: str, name: str, value: str, who: str = "민지") -> None:
    req = svc.stage("add", tenant, name, value, who)
    svc.apply(req["id"], who)


def test_env_mode_is_plain_getenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_X_API_KEY", DUMMY)
    assert resolver.get_secret("MARKETING_X_API_KEY") == DUMMY
    assert resolver.get_secret("NOPE_NAME", default="d") == "d"
    assert resolver.get_secret("A_NAME", env_fallback_name="MARKETING_X_API_KEY") == DUMMY
    assert resolver.startup_check() == "env"  # 마스터 키·DB가 없어도 된다


def test_env_mode_never_creates_the_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resolver.get_secret("ANY_NAME")
    resolver.startup_check()
    assert not (tmp_path / "secrets.db").exists()


def test_db_mode_roundtrip_and_tenant_split(monkeypatch: pytest.MonkeyPatch) -> None:
    db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    put(svc, "other", "MARKETING_VIDEO_API_KEY", DUMMY2)
    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", "ENV-VALUE-IGNORED-1")  # db 모드는 env를 안 본다
    assert resolver.startup_check() == "db"
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY", tenant="acme") == DUMMY
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY", tenant="other") == DUMMY2
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY", tenant="third") == ""
    monkeypatch.setenv("AX_SECRET_DEFAULT_TENANT", "acme")
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == DUMMY


def test_db_stores_only_ciphertext(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    svc.stage("replace", "acme", "MARKETING_VIDEO_API_KEY", DUMMY2, "민지")
    raw = (tmp_path / "secrets.db").read_bytes()
    wal = tmp_path / "secrets.db-wal"
    raw += wal.read_bytes() if wal.exists() else b""
    assert DUMMY.encode() not in raw and DUMMY2.encode() not in raw
    with sqlite3.connect(tmp_path / "secrets.db") as conn:
        dump = "\n".join(conn.iterdump())
    assert DUMMY not in dump and DUMMY2 not in dump
    row = svc.repo.get("acme", "MARKETING_VIDEO_API_KEY")
    assert row is not None and row.kid == "k1" and len(row.nonce) == 12
    assert row.hint == DUMMY[-4:]


def test_nonce_is_unique_and_aad_binds_tenant_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    ring = Keyring.from_env()
    a = ring.encrypt("acme", "SOME_KEY_NAME", DUMMY)
    b = ring.encrypt("acme", "SOME_KEY_NAME", DUMMY)
    assert a[1] != b[1] and a[2] != b[2]
    kid, nonce, blob = a
    assert ring.decrypt("acme", "SOME_KEY_NAME", kid, nonce, blob) == DUMMY
    for tenant, name in (("other", "SOME_KEY_NAME"), ("acme", "OTHER_NAME_X")):
        with pytest.raises(SecretStoreError) as err:
            ring.decrypt(tenant, name, kid, nonce, blob)
        assert err.value.code == errors.DECRYPT_FAILED
    assert DUMMY not in repr(ring) and "k1" in repr(ring)


def test_start_refused_with_reason_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.MASTER_KEY_MISSING
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", "short")
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.MASTER_KEY_INVALID
    key = generate_master_key()
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", key)
    put(SecretService.from_env(), "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())  # 다른 키
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.MASTER_KEY_MISMATCH
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", key)
    monkeypatch.setenv("AX_SECRET_MASTER_KEY_ID", "k2")  # 저장된 kid(k1)의 키가 없다
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.KID_UNKNOWN
    monkeypatch.delenv("AX_SECRET_MASTER_KEY_ID")
    monkeypatch.delenv("AX_SECRET_DB_FILE")
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.DB_PATH_MISSING
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path))  # 디렉터리라 못 연다
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.DB_UNAVAILABLE
    monkeypatch.setenv("AX_SECRET_STORE", "bogus")
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    assert err.value.code == errors.MODE_INVALID


def test_errors_and_logs_never_contain_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key = db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SecretStoreError) as err:
        resolver.startup_check()
    text = str(err.value) + repr(err.value)
    assert DUMMY not in text and key not in text
    resolver.reset_for_tests()
    resolver.get_secret("MARKETING_VIDEO_API_KEY", tenant="acme")  # 키가 틀려 못 읽는다
    assert DUMMY not in caplog.text and key not in caplog.text
    with pytest.raises(SecretStoreError) as err2:
        svc.stage("add", "acme", "MARKETING_NEW_API_KEY", "bad value\n", "민지")
    assert "bad value" not in str(err2.value)
    assert DUMMY not in repr(svc.repo.get("acme", "MARKETING_VIDEO_API_KEY"))


def test_runtime_keeps_last_values_when_db_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    db_mode(monkeypatch)
    put(SecretService.from_env(), "default", "MARKETING_VIDEO_API_KEY", DUMMY)
    resolver.startup_check()
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == DUMMY
    caplog.set_level(logging.WARNING)
    svc = resolver.service()

    def boom(*_a, **_k):
        raise SecretStoreError(errors.DB_UNAVAILABLE)

    monkeypatch.setattr(svc.repo, "list", boom)
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == DUMMY  # TTL 0 → 다시 읽다 실패
    assert errors.DB_UNAVAILABLE in caplog.text and DUMMY not in caplog.text


def test_replace_refreshes_cache_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "default", "MARKETING_VIDEO_API_KEY", DUMMY)
    resolver.startup_check()
    req = svc.stage("replace", "default", "MARKETING_VIDEO_API_KEY", DUMMY2, "민지")
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == DUMMY  # 승인 전에는 그대로
    svc.apply(req["id"], "민지")
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == DUMMY2  # TTL 0
    assert svc.repo.get("default", "MARKETING_VIDEO_API_KEY").version == 2
    d = svc.stage("delete", "default", "MARKETING_VIDEO_API_KEY", None, "민지")
    svc.apply(d["id"], "예진")
    assert resolver.get_secret("MARKETING_VIDEO_API_KEY") == ""


def test_stage_rules_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    cases = [
        (("add", "acme", "MARKETING_VIDEO_API_KEY", DUMMY), errors.ALREADY_EXISTS),
        (("replace", "acme", "MARKETING_NONE_API_KEY", DUMMY), errors.NOT_FOUND),
        (("add", "Bad Tenant", "MARKETING_X_API_KEY", DUMMY), errors.INVALID_TENANT),
        (("add", "acme", "lower_case", DUMMY), errors.INVALID_NAME),
        (("add", "acme", "MARKETING_X_API_KEY", "short"), errors.INVALID_VALUE),
        (("add", "acme", "MARKETING_X_API_KEY", "x" * 5000), errors.INVALID_VALUE),
        (("add", "acme", "MARKETING_X_API_KEY", " has-space-at-edge "), errors.INVALID_VALUE),
    ]
    for args, code in cases:
        with pytest.raises(SecretStoreError) as err:
            svc.stage(*args, "민지")
        assert err.value.code == code


def test_history_has_no_values_and_pending_view_has_no_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_mode(monkeypatch)
    svc = SecretService.from_env()
    put(svc, "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    req = svc.stage("replace", "acme", "MARKETING_VIDEO_API_KEY", DUMMY2, "민지")
    dumped = repr(svc.history(None)) + repr(svc.list_requests(None)) + repr(req)
    dumped += repr(svc.list_status(None)) + repr(svc.gate_params(svc.pending(req["id"])))
    assert DUMMY not in dumped and DUMMY2 not in dumped
    actions = [h["action"] for h in svc.history(None)]
    assert actions == ["request_replace", "add", "request_add"]
    assert all(h["by"] == "민지" and h["tenant"] == "acme" for h in svc.history(None))


def test_rewrap_after_master_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    old = db_mode(monkeypatch)
    put(SecretService.from_env(), "acme", "MARKETING_VIDEO_API_KEY", DUMMY)
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_MASTER_KEY_ID", "k2")
    monkeypatch.setenv("AX_SECRET_MASTER_KEYS_OLD", f"k1={old}")
    svc = SecretService.from_env()
    assert svc.reveal_for_runtime("acme", "MARKETING_VIDEO_API_KEY") == DUMMY
    assert svc.rewrap_all("cli") == 1
    assert svc.repo.get("acme", "MARKETING_VIDEO_API_KEY").kid == "k2"
    monkeypatch.delenv("AX_SECRET_MASTER_KEYS_OLD")
    assert SecretService.from_env().reveal_for_runtime("acme", "MARKETING_VIDEO_API_KEY") == DUMMY


def _envfile(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                f"MARKETING_VIDEO_API_KEY={DUMMY}",
                f'MARKETING_IMAGE_API_KEY="{DUMMY2}"',
                "AX_AGENT_TOKENS=a=DUMMY-TOKEN-AAAA,b=DUMMY-TOKEN-BBBB",
                "MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS=api.example.test",
                f"MARKETING_CUSTOM_FOO_KEY={DUMMY}",
                "AX_SESSION_SECRET=DUMMY-SESSION-SECRET-0001",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_migrate_cli_dry_run_import_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_mode(monkeypatch)
    env = str(_envfile(tmp_path))
    base = ["--tenant", "acme", "--env-file", env]
    assert migrate.main(["migrate", "--dry-run", *base]) == 0
    out = capsys.readouterr().out
    assert "would_import" in out and "total\t3" in out  # 세션 비밀키·허용 호스트·커스텀은 기본 제외
    assert "AX_SESSION_SECRET" not in out and "CUSTOM" not in out
    assert SecretService.from_env().list_status(None) == []  # dry-run은 쓰지 않는다
    assert migrate.main(["verify", *base]) == 0
    assert "missing_in_db" in capsys.readouterr().out
    assert migrate.main(["migrate", *base]) == 0
    out = capsys.readouterr().out
    assert out.count("imported") == 3
    assert migrate.main(["migrate", *base]) == 0
    assert "skipped_exists" in capsys.readouterr().out
    assert migrate.main(["verify", *base]) == 0
    assert capsys.readouterr().out.count("match") == 3
    # 이름 지정(커스텀 키), 환경변수 목록
    assert migrate.main(["migrate", *base, "--names", "MARKETING_CUSTOM_*"]) == 0
    assert "MARKETING_CUSTOM_FOO_KEY\timported" in capsys.readouterr().out
    # 값이 바뀌면 mismatch
    (tmp_path / ".env").write_text(f"MARKETING_VIDEO_API_KEY={DUMMY2}\n", encoding="utf-8")
    assert migrate.main(["verify", *base]) == 1
    assert "mismatch" in capsys.readouterr().out
    # env 값은 그대로(롤백 가능)
    assert DUMMY2 in (tmp_path / ".env").read_text(encoding="utf-8")


def test_migrate_cli_prints_no_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_mode(monkeypatch)
    env = str(_envfile(tmp_path))
    for cmd in (["migrate", "--dry-run"], ["migrate"], ["verify"]):
        migrate.main([*cmd, "--tenant", "acme", "--env-file", env])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    for secret in (DUMMY, DUMMY2, "DUMMY-TOKEN", "NOT-A-REAL"):
        assert secret not in text


def test_migrate_cli_reports_reason_code_without_master_key(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert migrate.main(["migrate", "--env-file", str(_envfile(tmp_path))]) == 2
    assert errors.MASTER_KEY_MISSING in capsys.readouterr().err
