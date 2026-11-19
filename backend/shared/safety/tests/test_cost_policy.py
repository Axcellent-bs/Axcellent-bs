"""비용 임계값 화면 변경: 승인 관문·요청자≠승인자·코드 상수 범위·mtime 재읽기·마케팅 불가."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import human_auth
from ax_contracts.schemas import ExecuteRequest
from ax_safety import cost_policy
from ax_safety.gate import Gate
from ax_safety.rules import load_thresholds

SECRET = "s" * 40  # 테스트용 자리값
BASE = "/api/safety/cost-policy"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "cost_policy.json"
    monkeypatch.setenv(cost_policy.FILE_ENV, str(path))
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    human_auth.reset_revoked_for_tests()
    return path


def _token(name: str, role: str = "admin") -> str:
    return human_auth.issue_session(account_id=f"acc-{name}", name=name, role=role)[0]


def _ask(client: Any, who: str, key: str, value: int, role: str = "admin") -> Any:
    body = {"rule_key": key, "requested_value": value, "reason": "시험"}
    return client.post(f"{BASE}/requests", json=body, headers=_bearer(_token(who, role)))


def _decide(client: Any, review_id: str, who: str, decision: str = "approved") -> Any:
    return client.post(
        f"/api/safety/reviews/{review_id}/decision",
        json={"decision": decision},
        headers=_bearer(_token(who)),
    )


def test_absolute_maxima_cover_the_defaults() -> None:
    table = load_thresholds()
    for spec in cost_policy.EDITABLE.values():
        rule = next(r for r in table.rules if r.key == spec.key)
        assert spec.minimum <= rule.value <= spec.absolute_max, spec.key


def test_request_is_held_and_nothing_changes_until_approved_and_applied(
    client: Any, gate: Gate, _files: Path
) -> None:
    held = _ask(client, "예진", "video_cost_single", 3000).json()["data"]
    assert held["status"] == "held"
    rule = next(r for r in gate.thresholds.rules if r.key == "video_cost_single")
    assert rule.value == 2000
    assert not _files.exists()
    pending = client.get(BASE).json()["data"]["requests"]
    assert [(r["status"], r["requested_by"]) for r in pending] == [("pending", "예진")]
    # 승인 전에는 적용되지 않는다
    early = client.post(
        f"{BASE}/requests/{held['review_id']}/apply", headers=_bearer(_token("재현"))
    )
    assert early.status_code == 409
    # 요청자 본인은 승인할 수 없다
    assert _decide(client, held["review_id"], "예진").status_code == 403
    assert _decide(client, held["review_id"], "재현").status_code == 200
    applied = client.post(
        f"{BASE}/requests/{held['review_id']}/apply", headers=_bearer(_token("재현"))
    )
    assert applied.status_code == 200
    assert applied.json()["data"]["new"] == 3000
    # 재시작 없이 같은 관문이 새 값을 쓴다
    assert next(r for r in gate.thresholds.rules if r.key == "video_cost_single").value == 3000
    view = client.get(BASE).json()["data"]
    row = next(i for i in view["items"] if i["key"] == "video_cost_single")
    assert (row["default"], row["value"], row["source"]) == (2000, 3000, "file")
    assert view["history"][0]["approved_by"] == "재현"
    again = client.post(
        f"{BASE}/requests/{held['review_id']}/apply", headers=_bearer(_token("재현"))
    )
    assert again.status_code == 409  # 같은 보류 건은 두 번 적용하지 않는다


def test_out_of_range_and_unknown_rules_are_refused_before_a_review_exists(
    client: Any, gate: Gate
) -> None:
    for key, value in (
        ("video_cost_single", 10_001),
        ("video_cost_single", 99),
        ("video_duration", 91),
        ("video_cost_single", 2000),
    ):
        assert _ask(client, "예진", key, value).status_code in (400, 409)
    assert _ask(client, "예진", "daily_count_x", 5).status_code == 400
    assert client.get("/api/safety/reviews?status=pending").json()["data"] == []


def test_only_logged_in_operator_or_admin_may_ask(client: Any, monkeypatch: Any) -> None:
    assert _ask(client, "회원", "video_cost_single", 3000, role="member").status_code == 403
    body = {"rule_key": "video_cost_single", "requested_value": 3000, "reason": "x"}
    assert client.post(f"{BASE}/requests", json=body).status_code == 403
    agent = {"Authorization": "Bearer mock:marketing"}
    assert client.post(f"{BASE}/requests", json=body, headers=agent).status_code == 403
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-test-token")
    shared = _bearer("shared-test-token")
    assert client.post(f"{BASE}/requests", json=body, headers=shared).status_code == 403


@pytest.mark.asyncio
async def test_agents_cannot_request_the_policy_action(gate: Gate) -> None:
    request = ExecuteRequest(
        action=cost_policy.ACTION,
        requester="marketing",
        context_id="ctx",
        idempotency_key="k1",
        params={"rule_key": "video_cost_single", "requested_value": 9000},
    )
    decision = await gate.execute(request)
    assert decision.status == "held"  # 미등록 요청자는 보류되고 어떤 값도 적용되지 않는다
    assert not cost_policy.applied_review_ids()


def test_file_values_are_reread_on_mtime_and_bad_values_are_ignored(_files: Path) -> None:
    base = load_thresholds()

    def value() -> int:
        return next(
            r for r in cost_policy.effective(base).rules if r.key == "video_cost_single"
        ).value

    assert value() == 2000
    _files.write_text(json.dumps({"values": {"video_cost_single": {"value": 2500}}}))
    os.utime(_files, ns=(1, 1))
    assert value() == 2500
    _files.write_text(json.dumps({"values": {"video_cost_single": {"value": 99999}}}))
    os.utime(_files, ns=(2, 2))
    assert value() == 2000  # 절대 상한 밖은 기본값
    _files.write_text("깨짐")
    os.utime(_files, ns=(3, 3))
    assert value() == 2000


def test_without_a_policy_file_changes_are_refused(client: Any, monkeypatch: Any) -> None:
    monkeypatch.delenv(cost_policy.FILE_ENV)
    monkeypatch.delenv("SAFETY_STATE_DB", raising=False)
    assert _ask(client, "예진", "video_cost_single", 3000).status_code == 400
    assert client.get(BASE).json()["data"]["editable"] is False
