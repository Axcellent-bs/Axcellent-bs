"""회사 연결 키·연결된 회사 카드 회사별 (여러 회사 설계 6.2, 2026-11-23).

- 키 목록은 설치에 하나(운영팀 발급). 연결된 회사는 회사마다 한 줄(캣북스는 예전 칸 `company`)
- 화면은 로그인 세션 회사의 줄만 읽고 쓴다. 공용 토큰은 캣북스(예전 그대로)
- 다른 회사 관리자가 키를 넣어도 캣북스 연결은 그대로, 캣북스 이름·업종·키 줄은 보이지 않는다
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies, human_auth
from ax_agent_template import company_connection as cc
from ax_dashboard_api import accounts
from ax_dashboard_api import connection_keys as ck
from ax_dashboard_api.main import app

BASE = "/api/dashboard/connection-keys"
CAT = {"name": "캣북스", "industry": "온라인 쇼핑몰", "network": "192.168.10.0/24"}
NUBI = {"name": "누비투어", "industry": "여행사", "network": "192.168.20.0/24"}
ROWS = [
    {"id": "acc-cat", "name": "캣", "email": "c@x.test", "role": "admin",
     "memberships": [{"company_id": "catbooks", "role": "admin"}]},
    {"id": "acc-nubi", "name": "누비", "email": "n@x.test", "role": "admin",
     "memberships": [{"company_id": "nubi", "role": "admin"}]},
    {"id": "acc-op", "name": "운영", "email": "o@x.test", "role": "operator",
     "memberships": [{"company_id": "catbooks", "role": "admin"}]},
]  # fmt: skip


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(tmp_path / "companies"))
    monkeypatch.delenv(ck.KEYS_FILE_ENV, raising=False)
    return tmp_path


@pytest.fixture
def people(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """캣북스 관리자·누비 관리자·운영자 로그인 세션 머리글, 공용 토큰 머리글(`shared`)."""
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
    out = {"shared": {"Authorization": "Bearer test-human-token"}}
    for who, (account, role, company) in {
        "cat": ("acc-cat", "admin", "catbooks"),
        "nubi": ("acc-nubi", "admin", "nubi"),
        "op": ("acc-op", "operator", "catbooks"),
    }.items():
        token, _ = human_auth.issue_session(
            account_id=account, name=who, role=role, company_id=company, company_role="admin"
        )
        out[who] = {"Authorization": f"Bearer {token}"}
    return out


@pytest.fixture
def client(people: dict[str, dict[str, str]]) -> TestClient:
    return TestClient(app)


def _issue(client: TestClient, people: dict, memo: str) -> dict:
    res = client.post(BASE + "/issue", json={"memo": memo}, headers=people["op"])
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _get(client: TestClient, who: dict) -> dict:
    res = client.get(BASE, headers=who)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _cat_connected(client: TestClient, people: dict) -> dict:
    """캣북스 관리자가 키로 연결해 둔 상태. 캣북스 키 줄을 돌려준다."""
    fresh = _issue(client, people, "캣북스 IT")
    res = client.post(BASE + "/connect", json={"key": fresh["key"], **CAT}, headers=people["cat"])
    assert res.status_code == 200, res.text
    return fresh


def _file(root: Path) -> dict:
    return json.loads((root / ck.KEYS_FILE).read_text(encoding="utf-8"))


# ---- 캣북스 그대로 ----


def test_catbooks_connect_card_and_disconnect_stay_the_same(client, people, root) -> None:
    fresh = _cat_connected(client, people)
    for who in ("cat", "shared"):  # 캣북스 관리자 세션·공용 토큰 둘 다 캣북스 카드
        state = _get(client, people[who])
        assert state["company"]["name"] == "캣북스"
        assert state["company"]["industry"] == "온라인 쇼핑몰"
        assert state["keys"][0]["status"] == "connected"
    # 파일 모양: 캣북스는 예전 칸 `company` 그대로(되돌리기·옛 코드가 읽을 수 있게)
    assert _file(root)["company"]["key_id"] == fresh["id"]
    # 로그인 전 화면도 캣북스(세션 없음 = 예전 규칙)
    public = TestClient(app, headers={"authorization": ""}).get("/api/dashboard/auth/company")
    assert public.json()["data"]["company"]["name"] == "캣북스"
    state = client.post(BASE + "/disconnect", headers=people["cat"]).json()["data"]
    assert state["company"] is None and state["keys"][0]["status"] == "issued"


# ---- 누비 관리자가 키를 넣어도 캣북스는 그대로 ----


def test_nubi_screen_does_not_show_catbooks(client, people) -> None:
    _cat_connected(client, people)
    state = _get(client, people["nubi"])
    assert state["company"] is None  # 누비는 아직 "연결 안 됨"
    assert state["keys"] == []  # 캣북스 키 줄(메모·회사 정보)도 안 보임
    text = json.dumps(state, ensure_ascii=False)
    assert "캣북스" not in text and "온라인 쇼핑몰" not in text


def test_nubi_cannot_take_the_catbooks_key(client, people, root) -> None:
    fresh = _cat_connected(client, people)
    check = client.post(BASE + "/check", json={"key": fresh["key"]}, headers=people["nubi"])
    assert check.json()["data"]["ok"] is False
    res = client.post(BASE + "/connect", json={"key": fresh["key"], **NUBI}, headers=people["nubi"])
    assert res.status_code == 422 and res.json()["error"]["code"] == "KEY_IN_USE"
    assert _get(client, people["cat"])["company"]["name"] == "캣북스"
    assert _file(root)["company"]["key_id"] == fresh["id"]


def test_nubi_connects_its_own_key_into_its_own_row(client, people, root) -> None:
    cat_key = _cat_connected(client, people)
    nubi_key = _issue(client, people, "누비 IT")
    body = {"key": nubi_key["key"], **NUBI}
    res = client.post(BASE + "/connect", json=body, headers=people["nubi"])
    assert res.status_code == 200, res.text
    nubi = res.json()["data"]
    assert nubi["company"]["name"] == "누비투어"
    assert [r["memo"] for r in nubi["keys"]] == ["누비 IT"]  # 자기 키 줄만
    # 캣북스 줄은 그대로
    cat = _get(client, people["cat"])
    assert cat["company"]["name"] == "캣북스" and cat["company"]["industry"] == "온라인 쇼핑몰"
    saved = _file(root)
    assert saved["company"]["key_id"] == cat_key["id"]
    assert saved["companies"]["nubi"]["key_id"] == nubi_key["id"]
    # 캣북스 화면에도 누비 이름이 섞이지 않음
    assert "누비투어" not in json.dumps(cat, ensure_ascii=False)
    # 운영자는 키 줄을 전부 봄(예전처럼)
    assert {r["memo"] for r in _get(client, people["op"])["keys"]} == {"캣북스 IT", "누비 IT"}


def test_nubi_info_and_disconnect_touch_only_nubi(client, people) -> None:
    _cat_connected(client, people)
    nubi_key = _issue(client, people, "누비 IT")
    client.post(BASE + "/connect", json={"key": nubi_key["key"], **NUBI}, headers=people["nubi"])
    edit = {**NUBI, "name": "누비투어 본사"}
    res = client.post(BASE + "/company", json=edit, headers=people["nubi"])
    assert res.json()["data"]["company"]["name"] == "누비투어 본사"
    state = client.post(BASE + "/disconnect", headers=people["nubi"]).json()["data"]
    assert state["company"] is None
    cat = _get(client, people["cat"])
    assert cat["company"]["name"] == "캣북스" and cat["keys"][0]["status"] == "connected"


def test_nubi_info_edit_without_its_own_connection_is_refused(client, people) -> None:
    _cat_connected(client, people)
    res = client.post(BASE + "/company", json={**NUBI}, headers=people["nubi"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_CONNECTED"
    # 누비가 끊기를 눌러도 캣북스는 그대로
    client.post(BASE + "/disconnect", headers=people["nubi"])
    assert _get(client, people["cat"])["company"]["name"] == "캣북스"


def test_a_key_catbooks_left_does_not_prefill_catbooks_info_for_nubi(client, people) -> None:
    fresh = _cat_connected(client, people)
    client.post(BASE + "/disconnect", headers=people["cat"])
    check = client.post(BASE + "/check", json={"key": fresh["key"]}, headers=people["nubi"])
    assert check.json()["data"] == {"ok": True, "reason": None, "company": None}
    # 캣북스가 다시 넣으면 예전처럼 미리 채움
    again = client.post(BASE + "/check", json={"key": fresh["key"]}, headers=people["cat"])
    assert again.json()["data"]["company"]["name"] == "캣북스"


def test_revoking_a_nubi_key_disconnects_only_nubi(client, people) -> None:
    _cat_connected(client, people)
    nubi_key = _issue(client, people, "누비 IT")
    client.post(BASE + "/connect", json={"key": nubi_key["key"], **NUBI}, headers=people["nubi"])
    client.post(BASE + "/revoke", json={"id": nubi_key["id"]}, headers=people["op"])
    assert _get(client, people["nubi"])["company"] is None
    assert _get(client, people["cat"])["company"]["name"] == "캣북스"


def test_old_file_without_company_ids_is_catbooks(client, people, root) -> None:
    """회사별 전의 파일(키 줄에 company_id 없음, `companies` 칸 없음)은 캣북스 것으로 읽는다."""
    old = {
        "keys": [
            {"id": "k1", "key": "AXC-AAAA-BBBB-CCCC", "memo": "옛 키", "issued_at": "2026-11-01",
             "status": "connected", "company": {**CAT, "site_url": ""}},
        ],
        "company": {"id": "c-k1", **CAT, "site_url": "", "key_id": "k1",
                    "connected_at": "2026-11-01"},
    }  # fmt: skip
    (root / ck.KEYS_FILE).write_text(json.dumps(old), encoding="utf-8")
    assert _get(client, people["cat"])["company"]["name"] == "캣북스"
    assert _get(client, people["shared"])["keys"][0]["memo"] == "옛 키"
    nubi = _get(client, people["nubi"])
    assert nubi["company"] is None and nubi["keys"] == []
    res = client.post(
        BASE + "/connect", json={"key": "AXC-AAAA-BBBB-CCCC", **NUBI}, headers=people["nubi"]
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "KEY_IN_USE"


def test_unconnected_key_is_seen_only_by_the_operator(client, people) -> None:
    """누비용으로 발급만 해 둔 키(아직 아무도 연결 안 함)는 운영자만 본다(#1645 사후 리뷰 1)."""
    _cat_connected(client, people)
    nubi_key = _issue(client, people, "누비 IT")
    cat = _get(client, people["cat"])
    assert [r["memo"] for r in cat["keys"]] == ["캣북스 IT"]  # 누비용 메모·가린 키는 안 보임
    assert "누비 IT" not in json.dumps(cat, ensure_ascii=False)
    assert _get(client, people["nubi"])["keys"] == []  # 연결 전이라 누비 관리자도 아직 안 봄
    assert {r["memo"] for r in _get(client, people["op"])["keys"]} == {"캣북스 IT", "누비 IT"}
    # 누비가 연결하면 누비에게만 보인다
    client.post(BASE + "/connect", json={"key": nubi_key["key"], **NUBI}, headers=people["nubi"])
    assert [r["memo"] for r in _get(client, people["nubi"])["keys"]] == ["누비 IT"]
    assert [r["memo"] for r in _get(client, people["cat"])["keys"]] == ["캣북스 IT"]


def test_catbooks_still_sees_its_own_key_after_disconnecting(client, people) -> None:
    """끊은 키는 회사 칸이 남아 그 회사에 계속 보인다(다시 연결할 때 미리 채움과 같은 이유)."""
    _cat_connected(client, people)
    client.post(BASE + "/disconnect", headers=people["cat"])
    rows = _get(client, people["cat"])["keys"]
    assert [(r["memo"], r["status"]) for r in rows] == [("캣북스 IT", "issued")]
    assert _get(client, people["nubi"])["keys"] == []
