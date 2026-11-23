"""코딩 에이전트가 회사 AI 키(키 저장소 tenant=회사)를 먼저 쓰고, 예전 파일 키는 옮긴다 (11/23).

값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_ai
from ax_agent_template.llm import LLMSettings
from ax_contracts.schemas.company_ai import CompanyAiCoder, CompanyAiSettings
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key
from dev_agent import coder, llm_key, migrate_llm_key
from dev_agent.main import app

CID = "catbooks"
COMPANY = "DUMMY-COMPANY-KEY-0001"
CODER = "DUMMY-CODER-KEY-0002"
LEGACY = "DUMMY-LEGACY-KEY-0003"
BASE = LLMSettings(model="server-model", base_url="https://server.example", api_key="server")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for var in ("AX_SECRET_STORE", "AX_SECRET_MASTER_KEY", "PLANNING_LLM_KEY_STORE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv("AX_COMPANY_AI_DIR", str(tmp_path / "company_ai"))
    monkeypatch.setenv("DEV_LLM_KEY_STORE", str(tmp_path / "llm_key.json"))
    monkeypatch.setattr(company_ai, "company_id", lambda: CID)
    resolver.reset_for_tests()
    llm_key.clear()
    yield tmp_path
    llm_key.clear()
    coder.reset_llm()
    resolver.reset_for_tests()


def _db_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    resolver.reset_for_tests()


def _put(name: str, value: str) -> None:
    svc = resolver.service()
    req = svc.stage("add", CID, name, value, "민지")
    svc.apply(req["id"], "민지")
    resolver.invalidate()


def _settings(**kw: object) -> None:
    folder = Path(os.environ["AX_COMPANY_AI_DIR"]) / CID
    folder.mkdir(parents=True, exist_ok=True)
    data = CompanyAiSettings.model_validate(kw).model_dump_json()
    (folder / "company_ai.json").write_text(data, encoding="utf-8")


def _legacy(path: Path, **kw: str) -> None:
    path.write_text(json.dumps({"api_key": LEGACY, **kw}), encoding="utf-8")


def test_env_mode_ignores_same_named_env_and_keeps_the_legacy_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LLM_API_KEY", "SERVER-DEFAULT-NOT-COMPANY"
    )  # env 모드 같은 이름 = 서버 기본 키
    assert company_ai.coder_llm(CID) is None
    llm_key.set_key(LEGACY)
    assert llm_key.apply(BASE).api_key == LEGACY
    assert "source" not in llm_key.public()  # 예전 파일 키는 예전 모양 그대로
    assert llm_key.legacy_writes_allowed() is True


def test_company_key_comes_before_the_legacy_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_mode(monkeypatch)
    llm_key.set_key(LEGACY, "https://legacy.example", "legacy-model")
    _settings(model="company-model", base_url="https://company.example")
    _put("LLM_API_KEY", COMPANY)
    got = llm_key.apply(BASE)
    assert (got.api_key, got.model, got.base_url) == (
        COMPANY,
        "company-model",
        "https://company.example",
    )
    shown = llm_key.public()
    assert shown["source"] == "company" and shown["key_hint"] == "••••0001"
    assert COMPANY not in json.dumps(shown)
    assert llm_key.active() is True


def test_separate_coder_key_uses_coder_model_then_company_then_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings(model="company-model", coder={"mode": "separate", "model": "coder-model"})
    _put("LLM_API_KEY", COMPANY)
    _put("DEV_CODER_LLM_API_KEY", CODER)
    got = llm_key.apply(BASE)
    assert (got.api_key, got.model, got.base_url) == (CODER, "coder-model", BASE.base_url)
    assert llm_key.public()["source"] == "coder"


def test_separate_mode_without_a_coder_key_falls_back_to_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings(coder={"mode": "separate"})
    _put("LLM_API_KEY", COMPANY)  # 따로 설정인데 코딩 키가 없으면 회사 키로 몰래 바꾸지 않는다
    assert llm_key.apply(BASE) == BASE
    assert llm_key.active() is False


def test_byok_route_points_to_company_ai_in_db_mode(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token")
    # db 모드면 테스트 토큰도 키 저장소에서 읽어 401이 나므로, 판단 함수만 db 모드처럼 둔다
    monkeypatch.setattr(llm_key, "legacy_writes_allowed", lambda: False)
    c = TestClient(app)
    url = "/api/planning/coding-settings/company-key"
    r = c.post(url, json={"api_key": LEGACY}, headers={"Authorization": "Bearer admin-test-token"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "MOVED_TO_COMPANY_AI"
    assert not Path(os.environ["DEV_LLM_KEY_STORE"]).exists()


def test_migrate_moves_the_legacy_file_into_the_coder_key(
    monkeypatch: pytest.MonkeyPatch, _env: Path
) -> None:
    _db_mode(monkeypatch)
    path = _env / "llm_key.json"
    _legacy(path, base_url="https://legacy.example", model="legacy-model")
    assert migrate_llm_key.migrate(dry_run=True) == "would_imported"
    assert path.exists() and company_ai.key("DEV_CODER_LLM_API_KEY", CID) == ""

    assert migrate_llm_key.migrate() == "imported"
    assert company_ai.key("DEV_CODER_LLM_API_KEY", CID) == LEGACY
    saved = company_ai.load_settings(CID).coder
    assert saved == CompanyAiCoder(
        mode="separate", model="legacy-model", base_url="https://legacy.example"
    )
    assert not path.exists() and (_env / "llm_key.json.moved").exists()
    history = (_env / "company_ai" / CID / "company_ai.history.jsonl").read_text("utf-8")
    assert LEGACY not in history and "migrate_llm_key" in history
    assert migrate_llm_key.migrate() == "no_legacy_file"


def test_migrate_refuses_a_different_coder_key_and_skips_the_company_key(
    monkeypatch: pytest.MonkeyPatch, _env: Path
) -> None:
    path = _env / "llm_key.json"
    _legacy(path)
    assert migrate_llm_key.migrate() == "secret_store_not_db"
    _db_mode(monkeypatch)
    _put("DEV_CODER_LLM_API_KEY", CODER)
    assert migrate_llm_key.migrate() == "conflict"
    assert path.exists() and company_ai.key("DEV_CODER_LLM_API_KEY", CID) == CODER

    monkeypatch.setenv("AX_SECRET_DB_FILE", str(_env / "secrets2.db"))
    resolver.reset_for_tests()
    _put("LLM_API_KEY", LEGACY)  # 회사 키와 같은 값이면 따로 키를 만들지 않는다
    assert migrate_llm_key.migrate() == "same_as_company"
    assert company_ai.key("DEV_CODER_LLM_API_KEY", CID) == ""
    assert company_ai.load_settings(CID).coder.mode == "company"
    assert not path.exists()
