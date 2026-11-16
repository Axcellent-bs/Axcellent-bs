"""판매 서버 OpenAPI 고정본 동기화 스크립트(scripts/sync_shop_openapi.py).

판매 서버 원본은 `Catbooks-demo/catbooks-demo`의 `backend/contracts/openapi/shop-api.json`이다.
AXcellent는 그 사본을 평가용 가짜 판매 서버 옆에 두고, 이 스크립트로 같은 커밋에서 다시 받는다.
네트워크는 쓰지 않는다(받아 오는 함수를 바꿔 끼운다).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sync_shop_openapi", BACKEND / "scripts" / "sync_shop_openapi.py"
)
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync  # dataclass가 모듈을 찾는다
_spec.loader.exec_module(sync)

OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
OLD = {"openapi": "3.1.0", "paths": {"/api/shop/orders": {"get": {}}}}
NEW = {
    "openapi": "3.1.0",
    "paths": {"/api/shop/orders": {"get": {}}, "/api/shop/failed-orders": {"get": {}}},
}


def _target(tmp_path: Path, pin: dict = OLD, sha: str = OLD_SHA):
    pin_path = tmp_path / "shop_api_openapi.json"
    pin_path.write_text(sync.dump(pin), encoding="utf-8")
    source_path = tmp_path / "fake_shop.py"
    source_path.write_text(
        '"""가짜."""\n\nOPENAPI_SOURCE = (\n'
        f'    "Catbooks-demo/catbooks-demo@{sha} "\n'
        '    "backend/contracts/openapi/shop-api.json"\n)\nX = 1\n',
        encoding="utf-8",
    )
    return sync.Target(pin=pin_path, source=source_path)


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> dict:
    """원본 레포 흉내: 이름(브랜치) → 커밋, 커밋 → 고정본."""
    world = {"refs": {"dev": NEW_SHA}, "files": {OLD_SHA: OLD, NEW_SHA: NEW}}
    monkeypatch.setattr(sync, "resolve_ref", lambda ref: world["refs"].get(ref, ref))
    monkeypatch.setattr(sync, "fetch_spec", lambda sha: world["files"][sha])
    return world


def test_pinned_sha_is_read_from_the_source_file(tmp_path: Path) -> None:
    assert sync.pinned_sha(_target(tmp_path)) == OLD_SHA


def test_sync_writes_the_new_spec_and_moves_the_source_line(tmp_path: Path, upstream) -> None:
    target = _target(tmp_path)
    assert sync.sync([target], "dev") == NEW_SHA
    assert json.loads(target.pin.read_text(encoding="utf-8")) == NEW
    text = target.source.read_text(encoding="utf-8")
    assert f"catbooks-demo@{NEW_SHA} " in text and OLD_SHA not in text
    assert text.endswith("X = 1\n")  # 나머지는 그대로


def test_check_passes_when_the_copy_matches_upstream(tmp_path: Path, upstream) -> None:
    target = _target(tmp_path, NEW, NEW_SHA)
    assert sync.check([target], "dev") == []


def test_check_reports_drift_with_the_changed_paths(tmp_path: Path, upstream) -> None:
    target = _target(tmp_path)
    problems = sync.check([target], "dev")
    assert len(problems) == 1
    assert "GET /api/shop/failed-orders" in problems[0] and "추가" in problems[0]


def test_check_catches_a_hand_edited_copy(tmp_path: Path, upstream) -> None:
    """적힌 커밋은 최신인데 파일을 손으로 고쳤으면 어긋났다고 본다."""
    target = _target(tmp_path, OLD, NEW_SHA)
    upstream["refs"]["dev"] = NEW_SHA
    assert sync.check([target], "dev")


def test_spec_diff_lists_added_and_removed_operations() -> None:
    lines = sync.spec_diff(NEW, OLD)
    assert lines == ["삭제: GET /api/shop/failed-orders"]


def test_the_finance_copy_is_one_of_the_targets() -> None:
    pins = {t.pin.relative_to(BACKEND).as_posix() for t in sync.TARGETS}
    assert "domains/finance/eval/shop_api_openapi.json" in pins
    for target in sync.TARGETS:
        assert target.pin.exists() and sync.pinned_sha(target)


def test_main_check_exit_code(tmp_path: Path, upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "TARGETS", [_target(tmp_path)])
    assert sync.main(["--check"]) == 1
    assert sync.main([]) == 0
    assert sync.main(["--check"]) == 0
