"""실제 모드 영역: 조직도의 `areas`를 명부에 넣는다."""

from pathlib import Path

from hr_agent.roster import store as roster
from hr_agent.roster.org_areas import load_org_areas, sync_areas
from hr_agent.store import Store

ORG = """
assignments:
  decisions: {unit: 인사총무}
areas:
  - payment
  - infra-ops
  - payment
"""


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "org.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_reads_areas_without_duplicates(tmp_path: Path) -> None:
    assert load_org_areas(_write(tmp_path, ORG)) == ["payment", "infra-ops"]


def test_no_areas_key_gives_nothing(tmp_path: Path) -> None:
    assert load_org_areas(_write(tmp_path, "assignments:\n  decisions: {unit: 인사총무}\n")) == []


def test_missing_file_gives_nothing(tmp_path: Path) -> None:
    assert load_org_areas(str(tmp_path / "없는파일.yaml")) == []


def test_no_path_gives_nothing(monkeypatch) -> None:
    monkeypatch.delenv("AX_ORG_CONFIG", raising=False)
    assert load_org_areas() == []


def test_bad_area_name_rejects_whole_list(tmp_path: Path) -> None:
    # 한 곳이라도 틀리면 일부만 넣지 않고 아무것도 넣지 않는다
    assert load_org_areas(_write(tmp_path, "areas:\n  - payment\n  - 결제\n")) == []
    assert load_org_areas(_write(tmp_path, "areas: payment\n")) == []


def test_sync_adds_only_missing_and_keeps_existing(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "hr.db"))
    assert roster.list_areas(store) == []
    assert sync_areas(store, ["payment", "search"]) == ["payment", "search"]
    assert sync_areas(store, ["payment", "search", "catalog"]) == ["catalog"]
    assert sync_areas(store, ["payment"]) == []
    assert roster.list_areas(store) == ["catalog", "payment", "search"]
    # 영역만 넣고 담당은 지어내지 않는다
    assert roster.entries(store) == []


def test_tenant_org_file_has_the_six_planning_areas() -> None:
    """회사 조직도의 영역은 기획 AI(`handover.py`)가 정하는 6개와 같다."""
    path = Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "org.yaml"
    assert load_org_areas(str(path)) == [
        "payment", "search", "catalog", "support-desk", "infra-ops", "inventory",
    ]  # fmt: skip
