"""새 회사를 붙여도 업종·인스타 레이더가 알아서 채워진다 (누비투어 여행사 기준).

1. 업종 "여행·숙박" (domain_profile.INDUSTRIES) → 쇼핑 짝 "여가/생활편의"(50000009)
2. 경쟁사 찾기(company_accounts.find)가 확인된 계정을 회사 연결 파일 옆
   instagram_radar_auto.json에 남긴다
3. 레이더 목록 우선순위: 화면 저장 > 회사 trends.toml > 자동 > 없음 (instagram.load_config)
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import tenant
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import DomainProfileRequest
from data_agent.connectors import CatalogProduct
from data_agent.skills import company_accounts as ca
from data_agent.skills import domain_profile as dp
from data_agent.skills import instagram as igs
from data_agent.skills import trend_report_company as trc
from data_agent.skills.product_link import CatalogRead

NOW = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
TENANTS = Path(__file__).resolve().parents[3] / "config/tenants"


def _about(name: str) -> str:
    return tomllib.loads((TENANTS / name / "tenant.toml").read_text("utf-8"))["profile"]["about"]


NUBI_ABOUT = _about("nubi")
CATBOOKS_ABOUT = _about("catbooks")

NUBI_CATALOG = CatalogRead(
    products=[CatalogProduct(product_id="t1", name="일본 오사카 3박4일 패키지",
                             category_code="jp"),
              CatalogProduct(product_id="t2", name="베트남 다낭 자유여행 패키지",
                             category_code="sea")],
    categories={"jp": "일본", "sea": "동남아"},
)  # fmt: skip
BOOK_CATALOG = CatalogRead(
    products=[CatalogProduct(product_id="b1", name="고양이 소설", category_code="novel")],
    categories={"novel": "소설"},
)


class _ProfileLLM(MockLLMClient):
    """업종을 소개 문장 근거와 함께 고르는 가짜 LLM. 프롬프트를 남긴다."""

    def __init__(self, industry: str, evidence: str) -> None:
        super().__init__()
        self.industry, self.evidence, self.prompt = industry, evidence, ""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.prompt = prompt
        return dp._Profile(
            industry=self.industry, customer_axes=["연령대", "성별"],
            evidence=[dp._FieldEvidence(field="industry", value=self.industry,
                                        evidence=self.evidence)],
        )  # fmt: skip


def _profile(tmp_path: Path, about: str, catalog: CatalogRead, llm: _ProfileLLM) -> Any:
    cfg = tmp_path / "tenant.toml"
    cfg.write_text(f'name = "회사"\n[profile]\nabout = "{about}"\n', encoding="utf-8")
    env = {"DATA_TENANT_CONFIG": str(cfg), "DATA_DOMAIN_PROFILE_DB": str(tmp_path / "p.db")}
    return asyncio.run(dp.make_profile(DomainProfileRequest(), llm, env=env, catalog=catalog,
                                       now=NOW)).output  # fmt: skip


# ---------------------------------------------------------------- 1. 업종 여행·숙박


def test_industry_list_has_travel_before_leisure_and_prompt_separates_them() -> None:
    names = list(dp.INDUSTRIES)
    assert names.index("여행·숙박") == names.index("여가·취미") - 1
    assert "여행·숙박" in dp.PROMPT  # 프롬프트 업종 설명이 목록을 그대로 쓴다
    assert "여행 상품·숙소·항공을 파는 회사는 여행·숙박" in dp.PROMPT
    assert "누비" not in dp.PROMPT and "캣북스" not in dp.PROMPT  # 회사 이름은 넣지 않는다
    assert set(dp.INDUSTRIES) <= set(trc.INDUSTRY_SHOPPING)


def test_nubi_profile_is_travel_and_shopping_pair_is_leisure(tmp_path: Path) -> None:
    llm = _ProfileLLM("여행·숙박", "해외 패키지 여행 상품")
    out = _profile(tmp_path, NUBI_ABOUT, NUBI_CATALOG, llm)
    assert out.industry == "여행·숙박" and out.industry_tags == ["여행·숙박"]
    assert "해외 패키지 여행 상품" in NUBI_ABOUT
    assert trc.INDUSTRY_SHOPPING["여행·숙박"] == "여가/생활편의"
    assert trc.shopping_category_of(out.industry) == "50000009"


def test_out_of_list_industry_is_still_dropped(tmp_path: Path) -> None:
    out = _profile(tmp_path, NUBI_ABOUT, NUBI_CATALOG, _ProfileLLM("여행", "해외 패키지 여행 상품"))
    assert out.industry is None and any("정해진 목록 밖" in x for x in out.limits)


def test_catbooks_profile_is_rebuilt_but_stays_publishing(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    llm = _ProfileLLM("출판·교육", "책을 파는 온라인 쇼핑몰")
    first = _profile(tmp_path, CATBOOKS_ABOUT, BOOK_CATALOG, llm)
    assert first.industry == "출판·교육"
    # 업종 목록이 바뀌면 저장 키가 바뀌어 다시 만든다 (저장분을 그대로 쓰지 않음)
    monkeypatch.setattr(dp, "INDUSTRIES", tuple(x for x in dp.INDUSTRIES if x != "여행·숙박"))
    old_key = dp.profile_hash(dp.TenantProfile(about=CATBOOKS_ABOUT), BOOK_CATALOG)
    monkeypatch.undo()
    new_key = dp.profile_hash(dp.TenantProfile(about=CATBOOKS_ABOUT), BOOK_CATALOG)
    assert old_key != new_key
    assert trc.shopping_category_of(first.industry) == trc.shopping_category_of("도서")


# ---------------------------------------------------------------- 2. 자동 목록 파일


class _BrandLLM:
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        return schema.model_validate({"items": [
            {"name_en": "hanatour", "reason": "여행사"},
            {"name_en": "modetour", "reason": "여행사"},
            {"name_en": "ghost travel", "reason": "없는 브랜드"},
        ]})  # fmt: skip


class _Graph:
    configured = True
    REAL = {"hanatour": 300, "modetour": 900}

    async def business_profile(self, username: str) -> dict[str, Any]:
        from data_agent.connectors.instagram_graph import IgError

        if username not in self.REAL:
            raise IgError("NO_DATA", "계정 없음")
        return {"username": username, "name": username, "followers_count": self.REAL[username],
                "media_count": 5}  # fmt: skip


class _Profile:
    tenant, catalog_hash, industry = "누비투어", "h1", "여행·숙박"
    product_groups: list = []
    seed_keywords: list = []


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AX_COMPANY_CONNECTION_FILE", str(tmp_path / "company_connection.json"))
    monkeypatch.setenv(tenant.TENANT_ENV, "nubi")
    for name in (tenant.IG_RADAR_FILE_ENV, tenant.IG_RADAR_AUTO_FILE_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _find(tmp_path: Path, accounts_cfg: str = "") -> ca.FoundAccounts:
    cfg = tmp_path / "tenant.toml"
    cfg.write_text(accounts_cfg, encoding="utf-8")
    env = {ca.TENANT_CONFIG_ENV: str(cfg)}
    return asyncio.run(ca.find(_Profile(), _BrandLLM(), _Graph(), env=env, now=NOW,
                               store=ca.AccountStore(":memory:")))  # fmt: skip


def test_find_writes_auto_file_with_verified_include_and_without_exclude(conn: Path) -> None:
    found = _find(conn, '[report.accounts]\ninclude = ["@nubi_pick"]\nexclude = ["hanatour"]\n')
    assert [a["account"] for a in found.accounts] == ["modetour", "hanatour"]  # 지어낸 ghost 없음
    path = conn / tenant.IG_RADAR_AUTO_FILE
    assert tenant.ig_radar_auto_file() == path
    data = json.loads(path.read_text("utf-8"))
    assert data["accounts"] == ["modetour", "nubi_pick"]  # 팔로워 큰 순 + include, exclude 빠짐
    assert data["industry"] == "여행·숙박" and data["rule_version"] == ca.RULE_VERSION == 3
    assert data["at"] == NOW.isoformat() and data["tenant"] == "nubi"
    assert tenant.ig_radar_auto_accounts() == ["modetour", "nubi_pick"]


def test_auto_file_from_another_tenant_is_not_used(conn: Path, monkeypatch) -> None:  # noqa: ANN001
    _find(conn)
    monkeypatch.setenv(tenant.TENANT_ENV, "catbooks")
    assert tenant.ig_radar_auto_accounts() is None


def test_without_connection_file_nothing_is_written(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    for name in ("AX_COMPANY_CONNECTION_FILE", tenant.IG_RADAR_AUTO_FILE_ENV):
        monkeypatch.delenv(name, raising=False)
    found = _find(tmp_path)
    assert found.accounts  # 찾기는 그대로 끝난다
    assert tenant.ig_radar_auto_file() is None and tenant.ig_radar_auto_accounts() is None
    assert not list(tmp_path.glob("instagram_radar_auto*"))


# ---------------------------------------------------------------- 3. load_config 우선순위


def _write(path: Path, accounts: list[str]) -> None:
    path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")


def test_load_config_priority_screen_file_auto_none(conn: Path) -> None:
    trends = conn / "trends.toml"
    env = {igs.TREND_CONFIG_ENV: str(trends)}
    # 없음: 회사 설정 파일 없음 · 경쟁사 찾기 전
    cfg = igs.load_config(env)
    assert cfg.radar_accounts == () and cfg.radar_source == "none"
    assert cfg.radar_missing == "레이더 계정이 없음 (회사 설정 파일 없음 · 경쟁사 찾기 전)"
    assert cfg == igs.IgConfig(radar_missing=cfg.radar_missing)  # 나머지는 IgConfig 기본값
    # 자동
    _write(conn / tenant.IG_RADAR_AUTO_FILE, ["auto_a", "auto_b"])
    cfg = igs.load_config(env)
    assert cfg.radar_accounts == ("auto_a", "auto_b") and cfg.radar_source == "auto"
    # 회사 trends.toml 목록이 자동보다 먼저
    trends.write_text('[instagram]\nradar_accounts = ["@file_a"]\nradar_batch_size = 7\n', "utf-8")
    cfg = igs.load_config(env)
    assert cfg.radar_accounts == ("file_a",) and cfg.radar_source == "tenant"
    assert cfg.radar_batch_size == 7
    # 화면 저장이 가장 먼저
    _write(conn / tenant.IG_RADAR_FILE, ["screen_a"])
    cfg = igs.load_config(env)
    assert cfg.radar_accounts == ("screen_a",) and cfg.radar_source == "screen"


def test_empty_auto_list_says_what_is_missing(conn: Path) -> None:
    trends = conn / "trends.toml"
    trends.write_text("[instagram]\nradar_accounts = []\n", "utf-8")
    _write(conn / tenant.IG_RADAR_AUTO_FILE, [])
    cfg = igs.load_config({igs.TREND_CONFIG_ENV: str(trends)})
    assert cfg.radar_accounts == ()
    assert cfg.radar_missing == ("레이더 계정이 없음 (회사 설정 radar_accounts 비어 있음"
                                 " · 경쟁사 찾기에서 확인된 계정 없음)")  # fmt: skip
    batch = asyncio.run(igs.radar_batch(cfg, None, None, NOW))  # type: ignore[arg-type]
    assert batch.status == "failed" and batch.why == cfg.radar_missing


def test_catbooks_keeps_its_file_list_even_with_an_auto_file(conn: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(tenant.TENANT_ENV, "catbooks")
    _write(conn / tenant.IG_RADAR_AUTO_FILE, ["auto_a"])
    path = TENANTS / "catbooks/trends.toml"
    listed = tomllib.loads(path.read_text("utf-8"))["instagram"]["radar_accounts"]
    cfg = igs.load_config({igs.TREND_CONFIG_ENV: str(path)})
    assert cfg.radar_source == "tenant" and cfg.radar_accounts == tuple(listed)
