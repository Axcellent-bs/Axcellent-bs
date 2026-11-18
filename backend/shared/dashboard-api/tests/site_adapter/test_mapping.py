"""짝 파일: 모델 읽기와 항목 꺼내기 규칙(점 경로, sum:, const:)."""

from pathlib import Path

import pytest

from ax_dashboard_api.site_adapter import mapping as m


def test_pick_dot_path() -> None:
    assert m.pick({"contact": {"name": "김*진"}}, "contact.name") == "김*진"


def test_pick_sum() -> None:
    assert m.pick({"adults": 2, "children": 1}, "sum:adults,children") == 3


def test_pick_const() -> None:
    assert m.pick({}, "const:paid") == "paid"


def test_pick_missing_raises() -> None:
    with pytest.raises(m.MissingField) as err:
        m.pick({"id": 1}, "code")
    assert err.value.field == "code"


def test_pick_null_value_is_none() -> None:
    assert m.pick({"member_id": None}, "member_id") is None


def test_load_nubi_mapping(adapter_dir: Path) -> None:
    found = m.load_mapping()
    assert found is not None
    assert found.resources["orders"].values["status"]["fully_paid"] == "paid"
    assert found.resources["products"].paging.since_param is None


def test_no_mapping_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(m.MAPPING_DIR_ENV, str(tmp_path))
    assert m.load_mapping() is None


def test_broken_mapping_raises(adapter_dir: Path) -> None:
    (adapter_dir / "mapping.json").write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(m.MappingError):
        m.load_mapping()
