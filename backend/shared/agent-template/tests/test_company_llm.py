"""AI 직원(총괄·인사·아카이브)이 회사 AI 키를 먼저 쓴다: `settings_for` (여러 회사 6+, 11/23).

값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ax_agent_template import company_ai
from ax_agent_template.llm import LLMSettings
from ax_contracts.schemas.company_ai import CompanyAiSettings
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key

CID = "catbooks"
COMPANY = "DUMMY-COMPANY-KEY-0001"
CODER = "DUMMY-CODER-KEY-0002"
ENV = {
    "LLM_MODEL": "server-model",
    "LLM_BASE_URL": "https://server.example",
    "LLM_API_KEY": "SERVER-DEFAULT-NOT-COMPANY",
    "EMBED_MODEL": "server-embed",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for var in ("AX_SECRET_STORE", "AX_SECRET_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv("AX_COMPANY_AI_DIR", str(tmp_path / "company_ai"))
    monkeypatch.setattr(company_ai, "company_id", lambda: CID)
    resolver.reset_for_tests()
    yield
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


def test_no_company_key_keeps_the_server_settings() -> None:
    """키 저장소가 db 모드가 아니면(env 모드의 같은 이름은 서버 기본 키) 그대로다."""
    assert company_ai.company_llm(CID) is None
    assert company_ai.settings_for("orchestrator", ENV) == LLMSettings.from_env("orchestrator", ENV)


def test_db_mode_without_a_company_key_keeps_the_server_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings(model="company-model")
    assert company_ai.company_llm(CID) is None
    assert company_ai.settings_for("hr", ENV).api_key == ENV["LLM_API_KEY"]


def test_company_key_model_and_address_win_over_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings(model="company-model", base_url="https://company.example")
    _put("LLM_API_KEY", COMPANY)
    got = company_ai.settings_for("orchestrator", ENV)
    assert (got.api_key, got.model, got.base_url) == (
        COMPANY,
        "company-model",
        "https://company.example",
    )


def test_empty_company_model_and_address_fall_back_to_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings()
    _put("LLM_API_KEY", COMPANY)
    got = company_ai.settings_for("hr", ENV)
    assert (got.api_key, got.model, got.base_url) == (COMPANY, "server-model", ENV["LLM_BASE_URL"])


def test_embedding_settings_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_mode(monkeypatch)
    _settings(model="company-model", embed_model="company-embed")  # 임베딩은 결정 대기
    _put("LLM_API_KEY", COMPANY)
    got = company_ai.settings_for("hr", ENV)
    assert got.embed_model == "server-embed"


def test_ai_staff_use_the_company_key_even_if_the_coder_is_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_mode(monkeypatch)
    _settings(model="company-model", coder={"mode": "separate", "model": "coder-model"})
    _put("LLM_API_KEY", COMPANY)
    _put("DEV_CODER_LLM_API_KEY", CODER)
    got = company_ai.settings_for("orchestrator", ENV)
    assert (got.api_key, got.model) == (COMPANY, "company-model")  # 코딩 키·모델은 개발 AI만


def test_no_current_company_keeps_the_server_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_mode(monkeypatch)
    _put("LLM_API_KEY", COMPANY)
    monkeypatch.setattr(company_ai, "company_id", lambda: None)  # 회사 연결 전
    assert company_ai.settings_for("hr", ENV).api_key == ENV["LLM_API_KEY"]


def test_company_key_is_not_in_the_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _db_mode(monkeypatch)
    _put("LLM_API_KEY", COMPANY)
    assert COMPANY not in repr(company_ai.company_llm(CID))
