"""마케팅 시험 공통 설정.

마케팅 시험은 캣북스 설치(`AX_TENANT=catbooks`, 레포의 `config/tenants/catbooks/tenant.toml`)로
돈다.
브랜드 태그·시연 안내·미디어 추천 분류는 코드가 아니라 회사 설정에서 오므로(11/24) 시험이 개발자
PC의 회사 연결·다른 회사 폴더를 읽지 않게 정해 둔다. 다른 회사를 보는 시험은 이 값을 바꾼다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _catbooks_company(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_TENANT", "catbooks")
    monkeypatch.delenv("AX_TENANTS_DIR", raising=False)
