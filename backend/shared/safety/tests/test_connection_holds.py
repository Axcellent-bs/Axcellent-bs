"""긴급 취약(KEV + 취약 버전)으로 알린 연결의 쓰기는 사람 보류로 보낸다 (#소통 11/03 합의)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ConnectionHoldNotice, HoldReason
from ax_safety.gate import Gate
from ax_safety.store import Store

DEV = {"Authorization": "Bearer mock:dev"}
URL = "/api/safety/connection-holds"


def _notice(hold: bool = True, connection_id: str = "conn-1") -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "hold": hold,
        "product": "gitlab",
        "version": "18.0.0",
        "kev_cves": ["CVE-2026-0001"] if hold else [],
        "judged_at": "2026-11-03T05:00:00+00:00",
        "reason": "KEV CVE-2026-0001",
    }


def _merge(act: Any, key: str, connection_id: str = "conn-1") -> Any:
    return act(
        "repo_comment",
        "planning",
        key,
        connection_id=connection_id,
    )


@pytest.mark.asyncio
async def test_held_connection_goes_to_a_person_and_approval_releases(gate: Gate, act: Any) -> None:
    gate.store.set_connection_hold(ConnectionHoldNotice.model_validate(_notice()))
    request = _merge(act, "c-1")
    held = await gate.execute(request)
    assert (held.grade, held.status) == ("C", "held")
    assert held.hold_reason == HoldReason.OVER_THRESHOLD  # 응답 enum은 늘릴 수 없다
    rule = gate.store.get_review(held.review_id).report["rules"][0]
    assert rule["key"] == "connection_kev_hold" and rule["kev_cves"] == ["CVE-2026-0001"]

    await gate.decide_review(held.review_id, "approved", "taeyoon", None)
    assert (await gate.execute(request)).status == "executed"


@pytest.mark.asyncio
async def test_only_the_held_connection_and_external_actions_stop(gate: Gate, act: Any) -> None:
    gate.store.set_connection_hold(ConnectionHoldNotice.model_validate(_notice()))
    other = await gate.execute(_merge(act, "c-2", connection_id="conn-2"))
    assert other.status == "executed"
    plain = await gate.execute(act("send_message", "planning", "m-1", connection_id="conn-1"))
    assert plain.status == "executed"  # 외부 연결 쓰기가 아니다


@pytest.mark.asyncio
async def test_release_lets_writes_through_again(gate: Gate, act: Any) -> None:
    gate.store.set_connection_hold(ConnectionHoldNotice.model_validate(_notice()))
    gate.store.set_connection_hold(ConnectionHoldNotice.model_validate(_notice(hold=False)))
    assert (await gate.execute(_merge(act, "c-3"))).status == "executed"


def test_only_the_dev_service_can_send_a_hold(client: TestClient) -> None:
    assert client.post(URL, json=_notice()).status_code == 401
    other = client.post(URL, json=_notice(), headers={"Authorization": "Bearer mock:marketing"})
    assert other.status_code == 403
    assert client.get(URL).json()["data"] == []

    assert client.post(URL, json=_notice(), headers=DEV).status_code == 200
    assert [n["connection_id"] for n in client.get(URL).json()["data"]] == ["conn-1"]
    assert client.post(URL, json=_notice(hold=False), headers=DEV).status_code == 200
    assert client.get(URL).json()["data"] == []


def test_hold_without_evidence_is_refused(client: TestClient) -> None:
    body = _notice() | {"kev_cves": []}
    assert client.post(URL, json=body, headers=DEV).status_code == 400
    assert client.get(URL).json()["data"] == []


def test_a_restart_keeps_the_hold_when_a_file_is_set(tmp_path: Any, monkeypatch: Any) -> None:
    path = tmp_path / "holds.json"
    monkeypatch.setenv("SAFETY_CONNECTION_HOLDS_FILE", str(path))
    Store().set_connection_hold(ConnectionHoldNotice.model_validate(_notice()))
    assert Store().connection_hold("conn-1") is not None
    Store().set_connection_hold(ConnectionHoldNotice.model_validate(_notice(hold=False)))
    assert Store().connection_hold("conn-1") is None


def test_a_broken_hold_file_stops_the_start(tmp_path: Any, monkeypatch: Any) -> None:
    path = tmp_path / "holds.json"
    path.write_text("망가짐", encoding="utf-8")
    monkeypatch.setenv("SAFETY_CONNECTION_HOLDS_FILE", str(path))
    with pytest.raises(ValueError):
        Store()
