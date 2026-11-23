"""회사별 회사 연결·사이트 변환기 (여러 회사 설계 6.2·6.3, 2026-11-23).

- 화면(회사 연결·변환기 설정)은 로그인 세션의 회사 줄만 읽고 쓴다. 공용 토큰은 캣북스(예전 그대로)
- 변환기 `/site-adapter/<회사 ID>/...`는 그 회사의 연결·짝·키·장부, `/site-adapter/...`는 캣북스
- 돈이 걸린 쓰기는 safety 기록의 회사(그 AI 요청의 meta.tenant_id)가 경로의 회사와 다르면 거절
- 캣북스 키(`AX_SITE_API_TOKEN`)·옛 변환기 폴더는 다른 회사에 쓰이지 않는다
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies, company_files, company_scope, human_auth
from ax_agent_template import company_connection as cc
from ax_agent_template import tenant as tenants
from ax_contracts.schemas import TENANT_ID_DEFAULT
from ax_dashboard_api import accounts, handoffs
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import company_route, gate_check
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

FIXTURES = Path(__file__).parent / "fixtures"
CAT_SITE = "http://10.0.0.8:3000"
NUBI_SITE = "http://10.0.0.9:3000"
CAT_KEY = "cat-site-key"
NUBI_KEY = "nubi-site-key"
ENV_KEY = "env-catbooks-key"
SCREEN = "/api/dashboard/site-adapter"
CONN = "/api/dashboard/company-connection"
ROWS = [
    {"id": "acc-cat", "name": "캣", "email": "c@x.test", "role": "admin",
     "memberships": [{"company_id": "catbooks", "role": "admin"}]},
    {"id": "acc-nubi", "name": "누비", "email": "n@x.test", "role": "admin",
     "memberships": [{"company_id": "nubi", "role": "admin"}]},
]  # fmt: skip


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """run_all.sh 모양: `.data/company_connection.json` + `.data/companies/` (회사 폴더 켬)."""
    data = tmp_path / ".data"
    data.mkdir()
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(data / "company_connection.json"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(data / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    monkeypatch.delenv(m.MAPPING_DIR_ENV, raising=False)
    monkeypatch.setattr(m, "DEFAULT_DIR", data / "site_adapter")
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.delenv(tenants.TENANT_ENV, raising=False)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.setenv(up.TOKEN_ENV, ENV_KEY)
    monkeypatch.setattr(cc, "_cache", None)
    company_files.reset_for_tests()
    return data


def _connect(company: str, site: str, key: str) -> Path:
    """그 회사 줄에 누비 짝·키·kind=adapter 연결을 깐다. 변환기 폴더를 돌려준다."""
    folder = m.adapter_dir(company)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "nubi_mapping.json", folder / m.MAPPING_FILE)
    (folder / up.TOKEN_FILE).write_text(key, encoding="utf-8")
    cc.save_connection(
        site,
        verified_at=None,
        updated_by="관리자",
        kind="adapter",
        adapter={"base_url": site},
        company_id=company,
    )
    return folder


class Sites:
    """고객사 둘 흉내: 주소(호스트)마다 받은 키를 적는다."""

    def __init__(self, nubi: Any) -> None:
        self.nubi = nubi
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        pages = {
            "/openapi.json": {"paths": {"/api/v1/bookings": {"get": {}}, "/api/v1/tours": {}}},
            "/api/v1/tours": self.nubi("nubi_tours.json"),
            "/api/v1/bookings": self.nubi("nubi_bookings_p1.json"),
        }
        body = pages.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    def keys(self) -> dict[str, str]:
        return {str(r.url.host): r.headers.get("authorization", "") for r in self.seen}


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch, nubi: Any) -> Sites:
    fake = Sites(nubi)
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def people(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """캣북스 관리자·누비 관리자 로그인 세션 머리글."""
    for name in (accounts.ACCOUNTS_FILE_ENV, "AX_COMPANIES_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name in (
        human_auth.REVOKED_FILE_ENV,
        human_auth.GENERATIONS_FILE_ENV,
        human_auth.ALLOW_SHARED_ENV,
        human_auth.LEGACY_COMPANY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    created = datetime(2026, 11, 23, tzinfo=UTC).isoformat()
    listed = [
        {"company_id": cid, "name": cid, "created_at": created} for cid in ("catbooks", "nubi")
    ]
    (root / "companies.json").write_text(json.dumps({"companies": listed}), encoding="utf-8")
    (root / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    human_auth.reset_revoked_for_tests()
    out = {}
    for who, (account, company) in {
        "cat": ("acc-cat", "catbooks"),
        "nubi": ("acc-nubi", "nubi"),
    }.items():
        token, _ = human_auth.issue_session(
            account_id=account, name=who, role="admin", company_id=company, company_role="admin"
        )
        out[who] = {"Authorization": f"Bearer {token}"}
    return out


@pytest.fixture
def client(root: Path, sites: Sites) -> Iterator[TestClient]:
    yield TestClient(app)


# ---- 경로 읽기 ----


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/site-adapter/nubi/api/shop/orders", ("nubi", "/site-adapter/api/shop/orders")),
        ("/site-adapter/nubi/health", ("nubi", "/site-adapter/health")),
        ("/site-adapter/shop-demo/api/shop/x", ("catbooks", "/site-adapter/api/shop/x")),
        ("/site-adapter/api/shop/orders", None),  # 예전 캣북스 주소
        ("/site-adapter/health", None),
        ("/site-adapter/NUBI/api/shop/orders", None),  # 회사 ID 형식이 아님
        ("/site-adapter/nubi/other", None),
        ("/api/dashboard/site-adapter", None),
    ],
)
def test_adapter_company_reads_the_path(path: str, expected: Any) -> None:
    assert company_route.adapter_company(path) == expected


# ---- 폴더 ----


def test_adapter_dir_is_per_company(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert m.adapter_dir() == root / "companies" / "catbooks" / "site_adapter"
    assert m.adapter_dir("nubi") == root / "companies" / "nubi" / "site_adapter"
    with company_scope.using("nubi"):
        assert m.adapter_dir() == root / "companies" / "nubi" / "site_adapter"
    # 캣북스 폴더 환경변수는 캣북스에만
    monkeypatch.setenv(m.MAPPING_DIR_ENV, str(root / "pinned"))
    assert m.adapter_dir() == root / "pinned"
    assert m.adapter_dir("nubi") == root / "companies" / "nubi" / "site_adapter"


def test_old_adapter_folder_goes_only_to_catbooks(root: Path) -> None:
    old = root / "site_adapter"
    old.mkdir()
    shutil.copy(FIXTURES / "nubi_mapping.json", old / m.MAPPING_FILE)
    with company_scope.using("nubi"):
        assert m.load_mapping() is None  # 다른 회사가 먼저 읽어도 옮기지 않는다
    assert old.exists()
    assert m.load_mapping() is not None  # 캣북스가 읽으면 캣북스로 옮긴다
    assert (root / "companies" / "catbooks" / "site_adapter" / m.MAPPING_FILE).exists()
    assert (root / "site_adapter.moved").exists()


def test_folders_off_keeps_catbooks_rollback(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = root / "site_adapter"
    old.mkdir()
    shutil.copy(FIXTURES / "nubi_mapping.json", old / m.MAPPING_FILE)
    assert m.load_mapping() is not None  # 옮김
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    assert m.adapter_dir() == old and (old / m.MAPPING_FILE).exists()  # 캣북스 되살림
    assert m.adapter_dir("nubi") == root / "companies" / "nubi" / "site_adapter"


def test_env_key_is_only_for_catbooks(root: Path) -> None:
    assert up.saved_token() == ENV_KEY  # 캣북스: 키 파일이 없으면 .env 키 (예전 그대로)
    with company_scope.using("nubi"):
        assert up.saved_token() == ""  # 다른 회사에 캣북스 키를 보내지 않는다
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    with company_scope.using("nubi"):
        assert up.saved_token() == NUBI_KEY


# ---- 변환기 읽기 ----


def test_reads_go_to_the_company_in_the_path(client: TestClient, sites: Sites) -> None:
    _connect("catbooks", CAT_SITE, CAT_KEY)
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    assert client.get("/site-adapter/api/shop/products").status_code == 200
    assert client.get("/site-adapter/nubi/api/shop/products").status_code == 200
    assert sites.keys() == {"10.0.0.8": f"Bearer {CAT_KEY}", "10.0.0.9": f"Bearer {NUBI_KEY}"}


def test_company_without_connection_is_not_configured(client: TestClient, sites: Sites) -> None:
    _connect("catbooks", CAT_SITE, CAT_KEY)
    res = client.get("/site-adapter/studyon/api/shop/products")
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_CONFIGURED"
    assert sites.seen == []  # 캣북스 사이트로 가지 않는다


# ---- 화면: 세션 회사 ----


def _mapping(nubi: Any) -> dict[str, Any]:
    raw = nubi("nubi_mapping.json")
    return {k: raw[k] for k in ("version", "auth_header", "auth_scheme", "resources")}


def test_screen_saves_and_reads_the_session_company(
    client: TestClient, people: dict[str, dict[str, str]], nubi: Any, root: Path
) -> None:
    cat_folder = _connect("catbooks", CAT_SITE, CAT_KEY)
    cat_mapping = (cat_folder / m.MAPPING_FILE).read_text(encoding="utf-8")
    body = {"base_url": NUBI_SITE, "token": NUBI_KEY, "mapping": _mapping(nubi)}
    res = client.put(SCREEN, json=body, headers=people["nubi"])
    assert res.status_code == 200, res.text
    # 누비 줄에만 저장
    nubi_folder = root / "companies" / "nubi" / "site_adapter"
    assert (nubi_folder / up.TOKEN_FILE).read_text(encoding="utf-8") == NUBI_KEY
    saved = cc.read_connection("nubi")
    assert saved is not None and saved.adapter == {"base_url": NUBI_SITE}
    # 캣북스 연결·짝·키 그대로
    cat = cc.read_connection("catbooks")
    assert cat is not None and cat.adapter == {"base_url": CAT_SITE}
    assert (cat_folder / m.MAPPING_FILE).read_text(encoding="utf-8") == cat_mapping
    assert (cat_folder / up.TOKEN_FILE).read_text(encoding="utf-8") == CAT_KEY
    # 읽기도 세션 회사
    nubi_state = client.get(CONN, headers=people["nubi"]).json()["data"]
    cat_state = client.get(CONN, headers=people["cat"]).json()["data"]
    assert nubi_state["connection"]["adapter"] == {"base_url": NUBI_SITE}
    assert nubi_state["shop_api_url"] == "http://dash:8020/site-adapter/nubi"
    assert cat_state["connection"]["adapter"] == {"base_url": CAT_SITE}
    assert cat_state["shop_api_url"] == "http://dash:8020/site-adapter"
    # 공용 토큰(세션 아님)은 캣북스 (예전 그대로)
    shared = client.get(CONN).json()["data"]
    assert shared["connection"]["adapter"] == {"base_url": CAT_SITE}


def test_screen_clear_only_touches_the_session_company(
    client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    _connect("catbooks", CAT_SITE, CAT_KEY)
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    assert client.post(CONN + "/clear", headers=people["nubi"]).status_code == 200
    assert cc.read_connection("nubi") is None
    assert cc.read_connection("catbooks") is not None
    res = client.post(CONN + "/mode", json={"mode": "internal"}, headers=people["cat"])
    assert res.status_code == 200
    cat = cc.read_connection("catbooks")
    assert cat is not None and cat.enabled is False


# ---- 돈이 걸린 쓰기: 경로의 회사 ≠ 기록의 회사면 거절 ----

KEY = "ctx_n:refund_request:claim:1"


def _record(tenant: str) -> dict[str, Any]:
    return {
        "execution_id": "exe_1",
        "request": {
            "action": "refund_request",
            "requester": "support",
            "context_id": "ctx_n",
            "tenant_id": tenant,
            "idempotency_key": KEY,
            "params": {"refund_amount": 1000},
        },
        "decision": {"status": "executed"},
    }


@pytest.fixture
def safety_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety.test:8001")
    monkeypatch.setenv("AX_AGENT_TOKENS", "support=sup-tok,dashboard-api=dash-tok")
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.setattr(
        gate_check,
        "TRANSPORT",
        httpx.MockTransport(lambda r: httpx.Response(200, json={"data": rows})),
    )
    return rows


async def _confirm() -> str:
    return await gate_check.confirm("refund", "support", "ctx_n", KEY, {"refund_amount": 1000})


@pytest.mark.asyncio
async def test_gate_takes_only_the_path_company_record(
    safety_records: list[dict[str, Any]],
) -> None:
    safety_records.append(_record("nubi"))
    with company_scope.using("nubi"):
        assert await _confirm() == "exe_1"
    with pytest.raises(ApiError) as caught:  # 캣북스 경로로 누비 기록을 쓰려 하면 거절
        await _confirm()
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_gate_refuses_a_catbooks_record_on_another_company_path(
    safety_records: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    safety_records.append(_record(TENANT_ID_DEFAULT))
    assert await _confirm() == "exe_1"  # 캣북스 경로: 예전 그대로
    with company_scope.using("nubi"), pytest.raises(ApiError) as caught:
        await _confirm()
    assert caught.value.status_code == 403 and caught.value.code == "GATE_NOT_PASSED"
    assert "tenant_mismatch" in caplog.text


def test_refund_route_with_other_company_record_is_refused(
    client: TestClient, safety_records: list[dict[str, Any]]
) -> None:
    """누비 경로로 온 환불인데 safety 기록이 캣북스 요청이면 403, 할 일함에도 넣지 않는다."""
    handoffs.reset_store()
    _connect("nubi", NUBI_SITE, NUBI_KEY)
    safety_records.append(_record(TENANT_ID_DEFAULT))
    res = client.post(
        "/site-adapter/nubi/api/shop/payments/pay_1/refund",
        json={"refund_amount": 1000, "idempotency_key": KEY, "context_id": "ctx_n"},
        headers={"authorization": "Bearer sup-tok"},
    )
    assert res.status_code == 403 and res.json()["error"]["code"] == "GATE_NOT_PASSED"
    assert handoffs.get_store().list("nubi") == []
    handoffs.reset_store()
