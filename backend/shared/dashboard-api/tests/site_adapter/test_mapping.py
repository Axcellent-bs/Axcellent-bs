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


ANSWER = {
    "method": "POST",
    "path": "/api/v1/inquiries/{site_id}/answer",
    "path_params": {"site_id": "link:inquiries.seq"},
    "body": {
        "answer": "view.answer",
        "code": "key_code:AX",
        "ends": "first:expires_at,now_plus:30",
        "once": "json:true",
        "seq": "path:seq",
    },
    "ok": [200],
}


def test_write_map_reads() -> None:
    found = m.WriteMap.model_validate(ANSWER)
    assert found.method == "POST" and found.path_params == {"site_id": "link:inquiries.seq"}


@pytest.mark.parametrize(
    "change",
    [
        {"method": "GET"},
        {"path": "https://evil.example/x"},
        {"path": "//evil.example/x"},
        {"path": "/api/v1/x?key=1"},
        {"path_params": {}},  # {site_id}에 짝이 없다
        {"path_params": {"site_id": "link:inquiries.seq", "extra": "a"}},
        {"ok": [500]},
        {"ok": []},
        {"body": {"code": "key_code:ax!"}},
        {"body": {"code": "key_code:" + "A" * 31}},
        {"body": {"at": "now_plus:soon"}},
        {"body": {"flag": "json:{not json"}},
        {"body": {"id": "link:inquiries"}},
        {"body": {"x": "a..b"}},
        {"body": {"x": ""}},
        {"unknown": 1},
    ],
)
def test_write_map_rejects_bad_shapes(change: dict) -> None:
    with pytest.raises(ValueError):
        m.WriteMap.model_validate({**ANSWER, **change})


def test_mapping_with_writes_and_unknown_action(adapter_dir: Path) -> None:
    import json

    path = adapter_dir / "mapping.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["writes"] = {"inquiry_answer": ANSWER}
    path.write_text(json.dumps(raw), encoding="utf-8")
    found = m.load_mapping()
    assert found is not None and set(found.writes) == {"inquiry_answer"}
    raw["writes"] = {"drop_table": ANSWER}  # 동작 이름은 코드에 고정된 것만
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(m.MappingError):
        m.load_mapping()
