"""계약 파일 안에 같은 이름의 클래스(또는 같은 이름 대입)가 두 번 생기지 않게.

2026-11-19: data.py에 새 `TrendEvidence`를 더하다 원래 `TrendEvidence`(trend_scan 근거)를 덮어써
data 테스트 138개가 깨졌다. 계약은 추가만 하므로 같은 이름이 다시 나오면 덮어쓰기다.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import ax_contracts.schemas as schemas

SCHEMA_DIR = Path(schemas.__file__).parent


def _top_level_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            names.append(node.name.id)
    return names


def test_no_duplicate_names_in_each_schema_file() -> None:
    files = sorted(SCHEMA_DIR.glob("*.py"))
    assert any(f.name == "data.py" for f in files)
    dupes: dict[str, list[str]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
        counts = Counter(_top_level_names(tree))
        # 클래스 이름이 두 번(클래스 둘, 또는 클래스와 같은 이름 대입) 나오면 덮어쓰기
        bad = sorted(name for name, n in counts.items() if n > 1 and name in classes)
        if bad:
            dupes[path.name] = bad
    assert dupes == {}, f"같은 이름 클래스가 다시 정의됨 (계약은 추가만): {dupes}"


def test_checker_catches_redefinition() -> None:
    tree = ast.parse("class A:\n    pass\n\n\nclass A:\n    pass\n\nB = 1\n")
    counts = Counter(_top_level_names(tree))
    assert counts["A"] == 2 and counts["B"] == 1
