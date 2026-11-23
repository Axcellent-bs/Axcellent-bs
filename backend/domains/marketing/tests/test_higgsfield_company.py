"""Higgsfield 키는 로그인한 회사 것을 먼저, 없으면 설치 기본 키를 쓴다(회사마다 계정이 다르다)."""

from __future__ import annotations

import pytest

import ax_secret_store
from ax_agent_template import company_ai
from marketing_agent import higgsfield_status as hs


def test_company_key_first_then_install_default(monkeypatch: pytest.MonkeyPatch) -> None:
    store = {
        ("nubi", hs.HIGGSFIELD_API_KEY_ENV): "DUMMY-NUBI",
        (None, hs.HIGGSFIELD_API_KEY_ENV): "DUMMY-BASE",
    }

    def fake(name: str, *, tenant: str | None = None, **_: object) -> str:
        return store.get((tenant, name), "")

    monkeypatch.setattr(hs, "get_secret", fake)
    monkeypatch.setattr(company_ai, "company_id", lambda: "nubi")
    assert hs.higgsfield_key() == "DUMMY-NUBI"
    monkeypatch.setattr(company_ai, "company_id", lambda: "catbooks")
    assert hs.higgsfield_key() == "DUMMY-BASE"
    assert ax_secret_store  # 소비자는 get_secret만 쓴다
