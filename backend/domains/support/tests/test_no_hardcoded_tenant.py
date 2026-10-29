"""고객지원 코드에 고객사 이름(캣북스)을 박지 않는다 (#43). 회사 이름은 설정에서만."""

from pathlib import Path

import pytest

import support_agent

PACKAGE = Path(support_agent.__file__).parent


@pytest.mark.parametrize("name", ["secure_transfer.py", "privacy_handlers.py"])
def test_support_code_has_no_hardcoded_tenant_name(name: str) -> None:
    assert "캣북스" not in (PACKAGE / name).read_text(encoding="utf-8")
