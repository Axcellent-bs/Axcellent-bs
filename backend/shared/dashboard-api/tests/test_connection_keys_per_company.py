"""회사 연결 키·연결된 회사 카드 회사별 (여러 회사 설계 6.2, 2026-11-23).

- 키 목록은 설치에 하나(운영팀 발급). 연결된 회사는 회사마다 한 줄(캣북스는 예전 칸 `company`)
- 화면은 로그인 세션 회사의 줄만 읽고 쓴다. 공용 토큰은 캣북스(예전 그대로)
- 다른 회사 관리자가 키를 넣어도 캣북스 연결은 그대로, 캣북스 이름·업종·키 줄은 보이지 않는다
"""

from __future__ import annotations

import json
import threading
import time
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
    # 끊은 키 줄은 연결 전으로 돌아가 운영자만 본다(사후 리뷰 11/23: 연결 전 키 줄은 운영자만)
    assert state["company"] is None and state["keys"] == []
    [row] = _get(client, people["op"])["keys"]
    assert row["status"] == "issued" and row["company"] is None


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
    # 끊을 때 키 줄의 회사 정보를 지워서(사후 리뷰 11/23) 캣북스가 다시 넣어도 미리 채우지 않는다
    again = client.post(BASE + "/check", json={"key": fresh["key"]}, headers=people["cat"])
    assert again.json()["data"] == {"ok": True, "reason": None, "company": None}


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


# ---- 사후 리뷰 11/23 ----


def test_a_key_issued_for_nubi_is_hidden_from_catbooks_until_connected(client, people) -> None:
    """연결 전 키 줄(company_id 없음 + issued)은 운영자·공용 토큰만 본다."""
    _cat_connected(client, people)
    nubi_key = _issue(client, people, "누비 IT 담당")
    cat = _get(client, people["cat"])
    assert [r["memo"] for r in cat["keys"]] == ["캣북스 IT"]  # 누비용 메모·가린 키 안 보임
    assert "누비 IT 담당" not in json.dumps(cat, ensure_ascii=False)
    assert _get(client, people["nubi"])["keys"] == []  # 연결 전에는 누비 관리자 목록에도 없다
    for who in ("op", "shared"):
        memos = {r["memo"] for r in _get(client, people[who])["keys"]}
        assert memos == {"캣북스 IT", "누비 IT 담당"}
    # 누비가 연결하면 누비 관리자에게 보이고, 캣북스에는 여전히 안 보인다
    client.post(BASE + "/connect", json={"key": nubi_key["key"], **NUBI}, headers=people["nubi"])
    assert [r["memo"] for r in _get(client, people["nubi"])["keys"]] == ["누비 IT 담당"]
    assert [r["memo"] for r in _get(client, people["cat"])["keys"]] == ["캣북스 IT"]


def test_old_catbooks_key_rows_stay_visible_but_unused_ones_do_not(client, people, root) -> None:
    """회사별 전 파일: 예전 칸 `company`의 키, 회사 정보가 남은 키(예전에 캣북스가 쓴 키)는
    캣북스 것. 회사 정보도 `company_id`도 없는 발급만 한 키는 운영자만 본다."""
    old = {
        "keys": [
            {"id": "k2", "key": "AXC-DDDD-EEEE-FFFF", "memo": "발급만", "issued_at": "2026-11-02",
             "status": "issued"},
            {"id": "k1", "key": "AXC-AAAA-BBBB-CCCC", "memo": "지금 키", "issued_at": "2026-11-01",
             "status": "connected", "company": {**CAT, "site_url": ""}},
            {"id": "k0", "key": "AXC-GGGG-HHHH-JJJJ", "memo": "예전 키", "issued_at": "2026-10-01",
             "status": "issued", "company": {**CAT, "site_url": ""}},
        ],
        "company": {"id": "c-k1", **CAT, "site_url": "", "key_id": "k1",
                    "connected_at": "2026-11-01"},
    }  # fmt: skip
    (root / ck.KEYS_FILE).write_text(json.dumps(old), encoding="utf-8")
    assert [r["memo"] for r in _get(client, people["cat"])["keys"]] == ["지금 키", "예전 키"]
    assert _get(client, people["nubi"])["keys"] == []
    every = [r["memo"] for r in _get(client, people["op"])["keys"]]
    assert every == ["발급만", "지금 키", "예전 키"]


def test_switching_keys_clears_the_old_key_row(client, people, root) -> None:
    first = _cat_connected(client, people)
    second = _issue(client, people, "캣북스 새 키")
    res = client.post(BASE + "/connect", json={"key": second["key"], **CAT}, headers=people["cat"])
    assert res.status_code == 200, res.text
    rows = {r["id"]: r for r in _file(root)["keys"]}
    assert rows[first["id"]]["status"] == "issued"
    assert "company_id" not in rows[first["id"]] and "company" not in rows[first["id"]]
    assert rows[second["id"]]["company_id"] == "catbooks"
    # 바꾼 예전 키는 연결 전 키라 캣북스 관리자 목록에서 빠진다
    assert [r["id"] for r in res.json()["data"]["keys"]] == [second["id"]]


def test_disconnect_clears_the_key_row_for_nubi_too(client, people, root) -> None:
    nubi_key = _issue(client, people, "누비 IT")
    client.post(BASE + "/connect", json={"key": nubi_key["key"], **NUBI}, headers=people["nubi"])
    assert _file(root)["keys"][0]["company_id"] == "nubi"
    client.post(BASE + "/disconnect", headers=people["nubi"])
    [row] = _file(root)["keys"]
    assert row["status"] == "issued" and "company_id" not in row and "company" not in row
    assert "누비투어" not in json.dumps(_file(root), ensure_ascii=False)
    # 그 키는 이제 아무 회사 것도 아니라 캣북스가 넣어도 누비 정보가 미리 채워지지 않는다
    check = client.post(BASE + "/check", json={"key": nubi_key["key"]}, headers=people["cat"])
    assert check.json()["data"] == {"ok": True, "reason": None, "company": None}


def test_two_saves_at_once_both_stay(root, monkeypatch: pytest.MonkeyPatch) -> None:
    """두 관리자가 동시에 저장해도 한쪽이 덮이지 않는다(잠금 + 임시 파일 교체)."""
    real_load = ck._load

    def slow_load() -> dict:
        data = real_load()
        time.sleep(0.2)  # 읽고 쓰기 사이를 벌려, 잠금이 없으면 뒤에 쓴 쪽이 앞의 저장을 덮게 한다
        return data

    monkeypatch.setattr(ck, "_load", slow_load)
    errors: list[BaseException] = []

    def run(memo: str) -> None:
        try:
            ck.issue(ck.IssueIn(memo=memo))
        except BaseException as exc:  # noqa: BLE001 - 스레드 안 실패를 시험으로 돌려준다
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(memo,)) for memo in ("가", "나")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert {r["memo"] for r in _file(root)["keys"]} == {"가", "나"}
    assert not (root / (ck.KEYS_FILE + ".tmp")).exists()  # 임시 파일은 바꿔 넣고 남지 않는다
