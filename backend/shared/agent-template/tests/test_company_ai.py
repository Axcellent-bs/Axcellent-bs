"""회사 AI 설정 읽기(서비스 공용): 저장 위치 규칙과 깨진 파일."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_agent_template import company_ai


def test_folder_follows_dashboard_rules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AX_COMPANY_AI_DIR", raising=False)
    monkeypatch.delenv("AX_COMPANY_CONNECTION_FILE", raising=False)
    assert company_ai.folder("catbooks") is None
    monkeypatch.setenv("AX_COMPANY_CONNECTION_FILE", str(tmp_path / "company_connection.json"))
    assert company_ai.folder("catbooks") == tmp_path / "company_ai" / "catbooks"
    monkeypatch.setenv("AX_COMPANY_AI_DIR", str(tmp_path / "ai"))
    assert company_ai.folder("catbooks") == tmp_path / "ai" / "catbooks"


def test_broken_settings_read_as_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AX_COMPANY_AI_DIR", str(tmp_path))
    (tmp_path / "catbooks").mkdir()
    (tmp_path / "catbooks" / "company_ai.json").write_text("{not json", encoding="utf-8")
    assert company_ai.load_settings("catbooks").coder.mode == "company"
    assert company_ai.coder_llm("catbooks") is None  # env 모드면 회사 키 없음
    assert company_ai.hint("DUMMY-KEY-NOT-REAL-9876") == "9876" and company_ai.hint("short") == ""
