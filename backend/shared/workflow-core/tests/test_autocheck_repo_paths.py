"""캣북스 레포 분리 뒤에도 보호 경로가 걸린다.

새 레포(Catbooks-demo/catbooks-demo)는 `backend/shop-api/`·`frontend/shop-web/` 구조다. 예전 구조
(`shop-api/`·`shop-web/`)와 새 구조 둘 다에서 되돌릴 수 없는 영역·머지 위험 경로가 같이 걸려야 한다.
허용 경로(`PR_ALLOWED_PATHS`)는 safety `thresholds.toml`과 같은 값이라 safety 변경과 함께 바꾼다.
"""

from __future__ import annotations

import pytest

from ax_workflow.autocheck import (
    IRREVERSIBLE_PREFIXES,
    MERGE_RISK_PREFIXES,
    irreversible_paths,
    merge_risk_paths,
)

NEW_LAYOUT = {"shop-api/": "backend/shop-api/", "shop-web/": "frontend/shop-web/"}


def _moved(prefix: str) -> str | None:
    for old, new in NEW_LAYOUT.items():
        if prefix.startswith(old):
            return new + prefix.removeprefix(old)
    return None


@pytest.mark.parametrize("prefix", [p for p in IRREVERSIBLE_PREFIXES if _moved(p)])
def test_irreversible_areas_are_caught_in_the_new_repo_layout(prefix) -> None:
    path = _moved(prefix) + "x.py"
    assert irreversible_paths([path]) == [path]


@pytest.mark.parametrize("prefix", [p for p in MERGE_RISK_PREFIXES if _moved(p)])
def test_merge_risk_paths_are_caught_in_the_new_repo_layout(prefix) -> None:
    moved = _moved(prefix)
    path = moved if not moved.endswith("/") else moved + "x.tsx"
    assert merge_risk_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "shop-api/payment/migrations/0042_drop.py",
        "shop-web/src/pages/Checkout.tsx",
        "shop-api/src/ax_shop_api/service.py",
    ],
)
def test_the_old_layout_is_still_caught(path) -> None:
    assert irreversible_paths([path]) or merge_risk_paths([path])


def test_the_new_repo_contract_pin_is_a_merge_risk() -> None:
    path = "backend/contracts/openapi/shop-api.json"
    assert merge_risk_paths([path]) == [path]


def test_ordinary_new_layout_files_are_not_flagged() -> None:
    paths = ["backend/shop-api/src/ax_shop_api/inbox.py", "frontend/shop-web/src/pages/Home.tsx"]
    assert irreversible_paths(paths) == []
    assert merge_risk_paths(paths) == []
