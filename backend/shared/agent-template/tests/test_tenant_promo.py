"""회사 홍보 영상 방향 `tenant.promo()` (11/24 "재미 먼저, 브랜드는 끝에").

화면 저장(회사별) → tenant.toml [brand] → 기본값(fun_first · end_card).
다른 회사 값은 섞이지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import tenant


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AX_TENANT", "AX_TENANTS_DIR", "AX_COMPANY_CONNECTION_FILE", "AX_PROMO_FILE"):
        monkeypatch.delenv(name, raising=False)


def _save(path: Path, companies: dict) -> None:
    path.write_text(json.dumps({"companies": companies}), encoding="utf-8")


def test_defaults_without_company_or_settings() -> None:
    assert tenant.promo() == {"promo_style": "fun_first", "brand_exposure": "end_card"}
    assert tenant.PROMO_DEFAULTS == tenant.promo()


def test_catbooks_file_and_brand_five_fields_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_TENANT", "catbooks")
    assert tenant.promo() == {"promo_style": "fun_first", "brand_exposure": "end_card"}
    # 같은 [brand] 섹션에 두 칸을 더해도 영상 브랜드 5칸은 그대로
    assert set(tenant.brand()) == set(tenant.BRAND_DEFAULTS)
    assert tenant.brand()["setting_ko"] == "아늑한 동네 책방"


def test_screen_save_beats_file_and_bad_values_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "promo_style.json"
    monkeypatch.setenv("AX_PROMO_FILE", str(path))
    monkeypatch.setenv("AX_TENANT", "catbooks")
    _save(path, {"catbooks": {"promo_style": "product", "brand_exposure": "made_up"}})
    # 모르는 값은 버리고 그 칸만 회사 설정 파일(end_card)
    assert tenant.promo() == {"promo_style": "product", "brand_exposure": "end_card"}
    path.write_text("{깨짐", encoding="utf-8")
    assert tenant.promo() == tenant.PROMO_DEFAULTS


def test_other_company_settings_do_not_mix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "promo_style.json"
    monkeypatch.setenv("AX_PROMO_FILE", str(path))
    _save(path, {"nubi": {"promo_style": "product", "brand_exposure": "caption"}})
    monkeypatch.setenv("AX_TENANT", "catbooks")
    assert tenant.promo() == tenant.PROMO_DEFAULTS  # 누비투어 값이 캣북스에 오지 않는다
    monkeypatch.setenv("AX_TENANT", "nubi")
    assert tenant.promo() == {"promo_style": "product", "brand_exposure": "caption"}
    assert tenant.promo("catbooks") == tenant.PROMO_DEFAULTS


def test_file_sits_next_to_company_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_COMPANY_CONNECTION_FILE", str(tmp_path / "company.json"))
    assert tenant.promo_file() == tmp_path / "promo_style.json"
