"""회사 설정 폴더(`config/tenants/<회사>/`): 카탈로그 + 커넥터 + 조직도가 한 폴더에 맞게 모였는지.

조직도가 없으면 꺼진 서비스로 가는 일이 `no_assignee`로 끝나므로, 커밋한 회사 폴더는
서비스 13개 모두 맡은 부서가 있어야 한다. run_all.sh가 쓰는 `scripts/tenant_env.py`의 우선순위
(환경변수 → config/org.yaml → 회사 폴더)도 본다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from ax_agent_template import routing
from ax_agent_template.services import SERVICES

BACKEND = Path(__file__).resolve().parents[1]
TENANTS = BACKEND / "config" / "tenants"
FAKE_TENANTS = BACKEND / "tests" / "tenants"  # 테스트 전용 가짜 회사(냉장 물류)


def _load_script():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(
        "tenant_env", BACKEND / "scripts" / "tenant_env.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tenant_env"] = module
    spec.loader.exec_module(module)
    return module


tenant_env = _load_script()


@pytest.mark.parametrize(
    "folder", [TENANTS / "catbooks", FAKE_TENANTS / "coldchain"], ids=lambda p: p.name
)
def test_a_tenant_folder_points_at_a_catalog_a_connector_and_an_org_chart(folder: Path) -> None:
    tenant = tenant_env.read_tenant(folder)
    assert tenant["connector"]
    org = routing.load_org(tenant["org"])
    missing = [s.id for s in SERVICES if s.id not in org]
    assert missing == [], f"조직도에 맡은 부서가 없는 서비스 (no_assignee로 실패): {missing}"


def test_catbooks_uses_the_catalog_where_it_already_is() -> None:
    tenant = tenant_env.read_tenant(TENANTS / "catbooks")
    assert tenant["catalog"] == "catbooks"  # data 카탈로그 위치는 그대로 (catalogs/catbooks.toml)
    assert tenant["connector"] == "shop-api"


def test_the_default_tenant_sets_org_and_catalog(tmp_path: Path) -> None:
    out, notes = tenant_env.resolve({}, local_org=tmp_path / "org.yaml")
    assert out["AX_TENANT"] == "catbooks"
    assert Path(out["AX_ORG_CONFIG"]) == (TENANTS / "catbooks" / "org.yaml").resolve()
    assert out["DATA_TENANT_CATALOG"] == "catbooks"
    assert not any(tenant_env.NO_ORG in n for n in notes)


def test_given_values_come_first(tmp_path: Path) -> None:
    """지금 쓰는 사람이 깨지지 않게: 환경변수와 config/org.yaml이 회사 폴더보다 먼저다."""
    mine = tmp_path / "org.yaml"
    mine.write_text("assignments:\n  incident: {unit: 내 부서}\n", encoding="utf-8")
    out, _ = tenant_env.resolve({}, local_org=mine)
    assert Path(out["AX_ORG_CONFIG"]) == mine.resolve()

    env = {"AX_ORG_CONFIG": str(mine), "DATA_TENANT_CATALOG": "other"}
    out, _ = tenant_env.resolve(env, local_org=tmp_path / "none.yaml")
    assert "AX_ORG_CONFIG" not in out and "DATA_TENANT_CATALOG" not in out  # 이미 준 값은 그대로


def test_missing_org_chart_is_said_plainly(tmp_path: Path) -> None:
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "tenant.toml").write_text(
        'catalog = "catbooks"\nconnector = "shop-api"\norg = "org.yaml"\n', encoding="utf-8"
    )
    out, notes = tenant_env.resolve(
        {"AX_TENANT": "acme"}, root=tmp_path, local_org=tmp_path / "none.yaml"
    )
    assert "AX_ORG_CONFIG" not in out
    assert any(n.startswith(tenant_env.NO_ORG) for n in notes)
    assert tenant_env.NO_ORG == "조직도 없음: 꺼진 서비스로 가는 일은 no_assignee로 실패합니다"


def test_a_connector_that_differs_from_the_catalog_is_refused(tmp_path: Path) -> None:
    (tmp_path / "tenant.toml").write_text(
        'catalog = "catbooks"\nconnector = "erp"\n', encoding="utf-8"
    )
    with pytest.raises(tenant_env.TenantError, match="connector"):
        tenant_env.read_tenant(tmp_path)


def test_an_unknown_tenant_is_refused(tmp_path: Path) -> None:
    with pytest.raises(tenant_env.TenantError, match="폴더가 없습니다"):
        tenant_env.resolve({"AX_TENANT": "nope"}, root=tmp_path)
