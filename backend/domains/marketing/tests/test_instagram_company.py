"""인스타 토큰·계정 ID는 로그인한 회사의 키 저장소에서 읽는다(회사마다 계정이 다르다)."""

from __future__ import annotations

import pytest

import ax_secret_store
from ax_agent_template import company_ai
from marketing_agent.sns_publish import instagram_graph as ig


def test_adapter_reads_the_logged_in_company_account(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str | None]] = []

    def fake(name: str, *, tenant: str | None = None, **_: object) -> str:
        seen.append((name, tenant))
        return {"nubi": f"DUMMY-{name}"}.get(tenant or "", "")

    monkeypatch.setattr(ax_secret_store, "get_secret", fake)
    monkeypatch.setattr(company_ai, "company_id", lambda: "nubi")
    adapter = ig.GraphInstagramAdapter.from_env()
    assert adapter._user_id == f"DUMMY-{ig.USER_ID_ENV}"  # noqa: SLF001
    assert {t for _, t in seen} == {"nubi"}

    monkeypatch.setattr(company_ai, "company_id", lambda: "catbooks")  # 연결 안 한 회사
    with pytest.raises(ig.GraphConfigError):
        ig.GraphInstagramAdapter.from_env()
