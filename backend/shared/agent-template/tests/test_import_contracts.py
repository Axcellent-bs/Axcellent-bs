"""domains끼리 import하지 않는지 `lint-imports`(backend/.importlinter)로 확인한다 (#42)."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONFIG = BACKEND_ROOT / ".importlinter"

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("importlinter") is None, reason="import-linter가 설치돼 있지 않다"
)


def _lint_imports(extra_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if extra_path is not None:
        # 앞에 둔 폴더의 패키지가 설치된 같은 이름 패키지를 가린다
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(extra_path), env.get("PYTHONPATH")]))
    code = "from importlinter.cli import lint_imports_command; lint_imports_command()"
    return subprocess.run(
        [sys.executable, "-c", code, "--config", str(CONFIG), "--no-cache"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_domains_do_not_import_each_other() -> None:
    result = _lint_imports()
    assert result.returncode == 0, f"lint-imports 결과:\n{result.stdout}\n{result.stderr}"
    assert "1 kept, 0 broken" in result.stdout


def test_a_domain_importing_another_domain_is_caught(tmp_path: Path) -> None:
    # 가짜 data_agent가 finance_agent를 import한다 → 계약이 깨져야 한다
    fake = tmp_path / "data_agent"
    fake.mkdir()
    (fake / "__init__.py").write_text("import finance_agent  # noqa: F401\n", encoding="utf-8")
    result = _lint_imports(extra_path=tmp_path)
    assert result.returncode != 0, result.stdout
    assert "data_agent -> finance_agent" in result.stdout
