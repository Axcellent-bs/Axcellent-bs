"""근무 상태·휴가: 기간만 기록하고, 교대 명부와 합쳐 근무 중·교대 외·휴가 중을 계산한다."""

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient

from hr_agent.main import app
from hr_agent.roster import leave_retention

_keys = count(1)


def _body(skill: str, inp: dict, token: str = "mock:dashboard-api", key: str | None = None) -> dict:
    return {
        "meta": {
            "context_id": "ctx_presence_1",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key or f"k{next(_keys)}",
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def client(svc) -> TestClient:
    return TestClient(app)


def leave(client, member="emp_01", start="2026-10-22", end="2026-10-23", key=None, **extra):
    inp = {"member_id": member, "start_date": start, "end_date": end, "recorded_by": "emp_hr"}
    return client.post("/a2a", json=_body("record_leave", inp | extra, key=key)).json()


def presence(client, details=False, area=None, at=None, token="mock:dashboard-api") -> dict:
    inp: dict = {"include_details": details}
    if area:
        inp["area"] = area
    if at:
        inp["at"] = at
    res = client.post("/a2a", json=_body("get_presence", inp, token)).json()
    assert res["status"] == "completed", res
    return res["output"]


def status_of(out: dict, member: str, area: str | None = None) -> str:
    found = [e["status"] for e in out["entries"] if e["member_id"] == member]
    assert found, member
    return found[0]


def test_교대_안이면_근무_중_밖이면_교대_외(client) -> None:
    # 시계는 2026-10-22 09:00 UTC. 시드: emp_01은 09~18 교대, emp_02는 18~09 교대
    out = presence(client)
    assert status_of(out, "emp_01") == "working"
    assert out["entries"][0].keys() == {"area", "role", "member_id", "status"}
    out = presence(client, at="2026-10-22T20:00:00Z")  # 교대가 끝난 뒤
    assert status_of(out, "emp_01") == "off_shift"
    assert status_of(out, "emp_02") == "working"


def test_휴가_기간이면_교대_안이어도_휴가_중이다(client) -> None:
    res = leave(client, "emp_01", "2026-10-22", "2026-10-23")
    assert (res["status"], res["outcome"]) == ("completed", "recorded")
    out = presence(client)
    assert status_of(out, "emp_01") == "on_leave"
    assert status_of(out, "emp_03") == "working"  # 다른 사람은 그대로


def test_휴가_날짜는_서울_기준이고_양_끝을_포함한다(client) -> None:
    leave(client, "emp_03", "2026-10-23", "2026-10-23")
    # 2026-10-22 15:30Z = 서울 10-23 00:30 → 휴가 첫날
    assert status_of(presence(client, at="2026-10-22T15:30:00Z"), "emp_03") == "on_leave"
    # 2026-10-23 15:30Z = 서울 10-24 00:30 → 휴가 끝난 뒤
    assert status_of(presence(client, at="2026-10-23T15:30:00Z"), "emp_03") != "on_leave"
    # 2026-10-22 14:59Z = 서울 10-22 23:59 → 휴가 전날
    assert status_of(presence(client, at="2026-10-22T14:59:00Z"), "emp_03") != "on_leave"


def test_상세는_요청했을_때만_담긴다(client) -> None:
    leave(client, "emp_01")
    plain = presence(client)
    assert plain["leaves"] == []
    assert "2026-10-23" not in str(plain) and "emp_hr" not in str(plain)
    detail = presence(client, details=True)
    assert [(v["member_id"], v["recorded_by"]) for v in detail["leaves"]] == [("emp_01", "emp_hr")]


def test_같은_기간을_다시_접수하면_처음_기록을_돌려준다(client) -> None:
    first = leave(client)
    again = leave(client)  # 다른 요청 번호, 같은 사람·기간
    assert again["outcome"] == "duplicate"
    assert again["output"]["leave"]["leave_id"] == first["output"]["leave"]["leave_id"]
    other = leave(client, "emp_02", "2026-12-01", "2026-12-02", key="same")
    retry = leave(client, "emp_02", "2026-12-01", "2026-12-02", key="same")  # 같은 요청 번호
    assert (other["outcome"], retry["outcome"]) == ("recorded", "duplicate")


def test_모르는_사람과_잘못된_입력은_기록하지_않는다(client, svc) -> None:
    assert leave(client, "emp_99")["outcome"] == "UNKNOWN_MEMBER"
    for bad in (
        {"start": "2026-10-24", "end": "2026-10-22"},
        {"start": "10/22", "end": "10/23"},
    ):
        res = leave(client, **bad)
        assert res["status"] in ("failed", "rejected"), res
    for extra in ({"reason": "병가"}, {"kind": "annual"}):
        assert leave(client, **extra)["status"] in ("failed", "rejected")
    assert svc.store.one("SELECT COUNT(*) AS n FROM leaves")["n"] == 0


def test_요청_번호가_없으면_기록하지_않는다(client, svc) -> None:
    body = _body(
        "record_leave",
        {
            "member_id": "emp_01",
            "start_date": "2026-10-22",
            "end_date": "2026-10-22",
            "recorded_by": "emp_hr",
        },
    )
    body["meta"]["idempotency_key"] = None
    res = client.post("/a2a", json=body).json()
    assert res["status"] in ("failed", "rejected")
    assert svc.store.one("SELECT COUNT(*) AS n FROM leaves")["n"] == 0


def test_인사가_정한_요청자만_부를_수_있다(client) -> None:
    for token in ("mock:orchestrator", "mock:finance", "mock:stranger"):
        inp = {
            "member_id": "emp_01",
            "start_date": "2026-10-22",
            "end_date": "2026-10-22",
            "recorded_by": "x",
        }
        res = client.post("/a2a", json=_body("record_leave", inp, token)).json()
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
        res = client.post("/a2a", json=_body("get_presence", {}, token)).json()
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")


def test_감사_기록에는_휴가_ID만_남고_사람과_기간은_없다(client, svc) -> None:
    leave(client, "emp_05", "2026-11-01", "2026-11-03")
    rows = svc.store.all("SELECT * FROM audit_logs")
    text = str(rows)
    assert "leave.recorded" in text
    assert "emp_05" not in text and "2026-11-03" not in text


def test_보유기간은_3년이고_지울_건수만_센다(client, svc, monkeypatch) -> None:
    leave(client, "emp_01", "2022-01-01", "2022-01-05")  # 3년 넘음 (오늘은 2026-10-22)
    leave(client, "emp_02", "2023-10-23", "2023-10-24")  # 3년 안
    assert leave_retention.retention_days() == 1095
    assert leave_retention.expired_count(svc) == 1
    assert svc.store.one("SELECT COUNT(*) AS n FROM leaves")["n"] == 2  # 아무것도 지우지 않는다


@pytest.mark.parametrize(
    ("raw", "days"), [("", 1095), ("abc", 1095), ("365", 1095), ("2000", 2000), ("99999", 1095)]
)
def test_보유_일수는_늘리는_것만_받는다(monkeypatch, raw: str, days: int) -> None:
    monkeypatch.setenv("HR_LEAVE_RETENTION_DAYS", raw)
    assert leave_retention.retention_days() == days


# ---- 취소 (cancel_leave) ---------------------------------------------------------------


def cancel(client, leave_id, by="emp_hr", token="mock:dashboard-api", key=None) -> dict:
    inp = {"leave_id": leave_id, "cancelled_by": by}
    return client.post("/a2a", json=_body("cancel_leave", inp, token, key)).json()


def test_취소하면_휴가_중이_아니게_되고_기록은_남는다(client, svc) -> None:
    made = leave(client, "emp_01", "2026-10-22", "2026-10-23")
    assert status_of(presence(client), "emp_01") == "on_leave"
    res = cancel(client, made["output"]["leave"]["leave_id"], by="emp_boss")
    assert (res["status"], res["outcome"]) == ("completed", "cancelled")
    gone = res["output"]["leave"]
    assert gone["cancelled_by"] == "emp_boss" and gone["cancelled_at"]
    assert status_of(presence(client), "emp_01") == "working"  # 교대 명부 상태로 돌아온다
    assert presence(client, details=True)["leaves"] == []  # 취소한 기록은 상세에서도 뺀다
    assert svc.store.one("SELECT COUNT(*) AS n FROM leaves")["n"] == 1  # 지우지 않는다


def test_취소한_뒤_같은_기간을_다시_넣으면_새_기록이_생긴다(client, svc) -> None:
    first = leave(client, "emp_01", "2026-10-22", "2026-10-23")["output"]["leave"]["leave_id"]
    cancel(client, first)
    again = leave(client, "emp_01", "2026-10-22", "2026-10-23")
    assert again["outcome"] == "recorded"  # 취소된 기록이 돌아오지 않는다
    new_id = again["output"]["leave"]["leave_id"]
    assert new_id != first and again["output"]["leave"]["cancelled_at"] is None
    assert status_of(presence(client), "emp_01") == "on_leave"
    assert svc.store.one("SELECT COUNT(*) AS n FROM leaves")["n"] == 2  # 이력이 그대로 남는다
    # 취소하지 않은 같은 기간을 또 넣으면 여전히 중복이다
    assert leave(client, "emp_01", "2026-10-22", "2026-10-23")["outcome"] == "duplicate"


def test_이미_취소된_기록을_다시_취소하면_성공으로_같은_기록을_돌려준다(client) -> None:
    made = leave(client)["output"]["leave"]["leave_id"]
    first = cancel(client, made, by="emp_a")
    again = cancel(client, made, by="emp_b")
    assert (again["status"], again["outcome"]) == ("completed", "duplicate")
    assert again["output"]["leave"]["cancelled_by"] == "emp_a"  # 처음 취소한 사람이 그대로다
    assert again["output"]["leave"]["cancelled_at"] == first["output"]["leave"]["cancelled_at"]


def test_없는_ID는_거절하고_사유_칸은_받지_않는다(client, svc) -> None:
    assert cancel(client, "lv_nope")["outcome"] == "UNKNOWN_LEAVE"
    made = leave(client)["output"]["leave"]["leave_id"]
    body = _body("cancel_leave", {"leave_id": made, "cancelled_by": "x", "reason": "실수"})
    res = client.post("/a2a", json=body).json()
    assert res["status"] in ("failed", "rejected")
    body = _body("cancel_leave", {"leave_id": made, "cancelled_by": "x"})
    body["meta"]["idempotency_key"] = None
    assert client.post("/a2a", json=body).json()["status"] in ("failed", "rejected")
    assert svc.store.one("SELECT cancelled_at FROM leaves")["cancelled_at"] is None


def test_취소도_인사가_정한_요청자만_부를_수_있다(client, svc) -> None:
    made = leave(client)["output"]["leave"]["leave_id"]
    for token in ("mock:orchestrator", "mock:finance", "mock:support", "mock:stranger"):
        res = cancel(client, made, token=token)
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    assert svc.store.one("SELECT cancelled_at FROM leaves")["cancelled_at"] is None


def test_취소_감사_기록에는_휴가_ID만_남는다(client, svc) -> None:
    made = leave(client, "emp_05", "2026-11-01", "2026-11-03")["output"]["leave"]["leave_id"]
    cancel(client, made, by="emp_boss")
    text = str(svc.store.all("SELECT * FROM audit_logs"))
    assert "leave.cancelled" in text
    assert "emp_05" not in text and "emp_boss" not in text and "2026-11-03" not in text


def test_취소해도_보유기간_건수는_그대로_센다(client, svc) -> None:
    made = leave(client, "emp_01", "2022-01-01", "2022-01-05")["output"]["leave"]["leave_id"]
    cancel(client, made)
    assert leave_retention.expired_count(svc) == 1
