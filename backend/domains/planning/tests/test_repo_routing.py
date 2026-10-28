"""연결(레포) 여러 개: PR마다 올릴 레포를 AI가 제안하고 사람이 승인한다.

실제 GitHub·LLM은 부르지 않는다. 연결은 목 종류(`mock`)로 넣고, LLM은 가짜 클라이언트를 끼운다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import MockLLMClient
from planning_agent import repo_routing, vcs, vcs_bridge
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.main import app
from planning_agent.repo_routing import Candidate, RepoPick, RouteInput, score_rules
from planning_agent.runtime import set_service

API = "shop-api-conn"
WEB = "shop-web-conn"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    vcs.set_vcs(None)
    repo_routing.set_llm(None)  # 실제 LLM은 부르지 않는다
    yield
    vcs._screen = None
    vcs.set_vcs(None)
    repo_routing.reset_llm()


def _two() -> None:
    vcs.upsert_connection(
        id=API, kind="mock", repo="acme/shop-api", paths=["shop-api/"], description="주문 API"
    )
    vcs.upsert_connection(
        id=WEB, kind="mock", repo="acme/shop-web", paths=["shop-web/"], description="쇼핑몰 화면"
    )


def _writes(cid: str) -> list:
    return vcs.get_adapter(cid).writes  # type: ignore[attr-defined]


def _req12_to_prs(service: PlanningService) -> None:
    run(service.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    run(service.confirm_spec("SPEC-7"))
    run(service.generate_prs("SPEC-7"))


def _pr(service: PlanningService, pid: str):
    return next(p for p in service.state.prs if p.id == pid)


# ---------------------------------------------------------------- 규칙 점수

CANDS = [
    Candidate("c1", "acme/shop-api", "주문 API", ("shop-api/",)),
    Candidate("c2", "acme/shop-web", "쇼핑몰 화면", ("shop-web/",)),
]


def test_rules_path_match_is_high() -> None:
    j = score_rules(RouteInput(changed_paths=("shop-web/checkout/Fail.tsx",)), CANDS)
    assert (j.connection_id, j.confidence) == ("c2", "high")
    assert j.reasons[0] == "바꿀 경로 shop-web/checkout/Fail.tsx → 담당 경로 shop-web/"
    # 경로 +3, 경로 첫 폴더의 낱말 shop·web이 레포 이름에 있어 +2
    assert j.candidates[0] == {"connection_id": "c2", "repo": "acme/shop-web", "score": 5}
    assert j.candidates[1]["score"] == 1


def test_rules_keyword_only_is_mid() -> None:
    cands = [Candidate("c1", "acme/backend", "정산"), Candidate("c2", "acme/web", "쇼핑몰 화면")]
    j = score_rules(RouteInput(title="쇼핑몰 배너 수정"), cands)
    assert (j.connection_id, j.confidence) == ("c2", "mid")
    assert any("쇼핑몰" in r for r in j.reasons)


def test_rules_no_signal_is_low_and_first() -> None:
    cands = [Candidate("c1", "acme/a"), Candidate("c2", "acme/b")]
    j = score_rules(RouteInput(title="무관한 작업"), cands)
    assert (j.connection_id, j.confidence) == ("c1", "low")
    assert j.reasons == ["근거 없음 — 사람 확인 필요"]


# ---------------------------------------------------------------- LLM 판단


def test_llm_valid_pick_marks_reasons() -> None:
    fake = MockLLMClient(
        responses={RepoPick: RepoPick(connection_id="c1", reasons=["주문 흐름 변경"])}
    )
    inp = RouteInput(title="주문 버튼", changed_paths=("shop-api/order.py",))
    j = run(repo_routing.decide(inp, CANDS, fake))
    assert (j.connection_id, j.by, j.confidence) == ("c1", "llm", "high")
    assert j.reasons[0] == "AI: 주문 흐름 변경"
    # 토큰 같은 비밀 값은 보내지 않고 후보 이름·경로만 보낸다
    assert "acme/shop-api" in fake.calls[0] and "token" not in fake.calls[0]


def test_llm_invalid_id_falls_back_to_rules() -> None:
    fake = MockLLMClient(responses={RepoPick: RepoPick(connection_id="nope", reasons=["x"])})
    inp = RouteInput(changed_paths=("shop-web/a.tsx",))
    j = run(repo_routing.decide(inp, CANDS, fake))
    assert (j.connection_id, j.by) == ("c2", "rules")
    assert not any(r.startswith("AI:") for r in j.reasons)


def test_llm_error_falls_back_to_rules() -> None:
    fake = MockLLMClient()  # 응답 등록 없음 → 오류
    j = run(repo_routing.decide(RouteInput(changed_paths=("shop-web/a.tsx",)), CANDS, fake))
    assert j.by == "rules" and j.connection_id == "c2"


# ---------------------------------------------------------------- 연결 목록


def test_registry_crud_hides_token_and_keeps_it() -> None:
    secret = "ghp_secretvalue9876"
    a = vcs.upsert_connection(
        kind="github", repo="acme/shop", token=secret, paths=["/shop-web/", ""]
    )
    b = vcs.upsert_connection(kind="mock", repo="acme/other")
    assert (a.id, b.id) == ("conn-1", "conn-2")
    assert secret not in repr(a)
    rows = vcs.list_connections()
    assert [r["id"] for r in rows] == ["conn-1", "conn-2"]
    assert rows[0]["paths"] == ["shop-web/"] and rows[0]["token_hint"] == "••••9876"
    assert secret not in str(rows)
    # 토큰을 비워 다시 저장하면 이전 토큰을 쓴다
    vcs.upsert_connection(id="conn-1", kind="github", repo="acme/web", token="")
    row = vcs.list_connections()[0]
    assert row["repo"] == "acme/web" and row["token_set"]
    # 틀린 설정은 거부하고 이전 값을 둔다
    with pytest.raises(vcs.VcsError):
        vcs.upsert_connection(id="conn-1", kind="github", repo="bad")
    assert vcs.list_connections()[0]["repo"] == "acme/web"
    assert vcs.delete_connection("conn-2") and not vcs.delete_connection("conn-2")
    assert [r["id"] for r in vcs.list_connections()] == ["conn-1"]


def test_env_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_KIND", "github")
    monkeypatch.setenv("PLANNING_VCS_REPO", "acme/env")
    monkeypatch.setenv("PLANNING_VCS_TOKEN", "fake-env-token-4321")
    vcs.upsert_connection(kind="mock", repo="acme/screen")
    rows = vcs.list_connections()
    assert [(r["id"], r["source"]) for r in rows] == [("conn-1", "screen"), ("env", "env")]
    assert rows[1]["token_hint"] == "••••4321" and "fake-env-token" not in str(rows)
    with pytest.raises(vcs.VcsError):
        vcs.delete_connection("env")
    with pytest.raises(vcs.VcsError):
        vcs.upsert_connection(id="env", kind="mock")


# ---------------------------------------------------------------- REST


def test_connections_api() -> None:
    c = TestClient(app)
    assert c.get("/api/planning/vcs/connections").json()["data"] == []
    secret = "ghp_apisecret5555"
    r = c.post(
        "/api/planning/vcs/connections",
        json={"kind": "github", "repo": "acme/shop-web", "token": secret, "paths": ["shop-web/"]},
    )
    assert r.status_code == 200 and secret not in r.text
    row = r.json()["data"][0]
    assert set(row) == {
        "id", "kind", "repo", "base_url", "base_branch", "description",
        "paths", "mount", "token_set", "token_hint", "source",
    }  # fmt: skip
    assert (row["id"], row["token_set"], row["source"]) == ("conn-1", True, "screen")
    r = c.post("/api/planning/vcs/connections", json={"kind": "mock", "repo": "acme/api"})
    assert [x["id"] for x in r.json()["data"]] == ["conn-1", "conn-2"]
    bad = c.post("/api/planning/vcs/connections", json={"kind": "github", "repo": "x"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_VCS"
    bad = c.post("/api/planning/vcs/connections", json={"kind": "mock", "paths": "shop/"})
    assert bad.status_code == 400
    t = c.post("/api/planning/vcs/connections/conn-2/test").json()["data"]
    assert t["ok"] is True and t["connection_id"] == "conn-2" and "checked_at" in t
    assert c.post("/api/planning/vcs/connections/zzz/test").status_code == 404
    # 예전 화면 길은 첫 연결을 보여 준다
    assert c.get("/api/planning/vcs").json()["data"]["repo"] == "acme/shop-web"
    d = c.post("/api/planning/vcs/connections/conn-1/delete")
    assert [x["id"] for x in d.json()["data"]] == ["conn-2"]
    nf = c.post("/api/planning/vcs/connections/conn-1/delete")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- 도메인 흐름


def test_two_connections_propose_then_approve(service: PlanningService) -> None:
    _two()
    _req12_to_prs(service)
    pr31 = _pr(service, "PR #31")
    t = pr31.target
    assert t is not None and (t.connection_id, t.status, t.confidence) == (API, "proposed", "high")
    assert t.decided_by is None and t.candidates[0].connection_id == API
    # 승인 전: 어디에도 올리지 않고 머지도 안 함
    assert _writes(API) == [] and _writes(WEB) == []
    assert not pr31.merged and pr31.vcs_number is None
    task = next(x for x in service.state.tasks if x.id == pr31.task)
    assert task.state == "머지 대기"
    points = [a.point for a in service.state.activity]
    assert "vcs.repo_proposed" in points and "vcs.repo_pending" in points
    line = next(a.text for a in service.state.activity if a.point == "vcs.repo_proposed")
    assert line.startswith("PR #31 → acme/shop-api 제안 (확신 high) · 근거 바꿀 경로")
    # 승인 → 그 연결에 올리고 자동 머지 규칙대로 병합
    run(service.approve_repo("PR #31"))
    pr31 = _pr(service, "PR #31")
    assert pr31.target.status == "approved" and pr31.target.decided_by == "노예진"
    assert pr31.merged and pr31.vcs_number == 1
    assert _writes(API) == [("open_change", 1), ("merge_change", 1)] and _writes(WEB) == []
    assert any(g.action == "approve_repo" for g in service.gate_calls)
    # SSE 상태에도 실린다
    dumped = next(p for p in service.snapshot()["prs"] if p["id"] == "PR #31")
    assert dumped["target"]["connection_id"] == API


def test_reject_then_approve_other_connection(service: PlanningService) -> None:
    _two()
    _req12_to_prs(service)
    assert _pr(service, "PR #32").target.connection_id == WEB
    run(service.reject_repo("PR #32"))
    pr32 = _pr(service, "PR #32")
    assert pr32.target.status == "rejected" and pr32.vcs_number is None
    with pytest.raises(ActionError) as err:
        run(service.merge_pr("PR #32"))
    assert err.value.code == "REPO_NOT_APPROVED"
    run(service.approve_repo("PR #32", API))
    pr32 = _pr(service, "PR #32")
    assert (pr32.target.connection_id, pr32.target.status) == (API, "approved")
    assert pr32.target.reasons[0].startswith("사람이 고름")
    assert _writes(API) == [("open_change", 1)] and _writes(WEB) == []
    assert not pr32.merged  # 검수 불일치 건은 예전처럼 사람 머지
    run(service.merge_pr("PR #32"))
    assert _pr(service, "PR #32").merged and _writes(API)[-1] == ("merge_change", 1)
    with pytest.raises(ActionError):
        run(service.approve_repo("PR #32", WEB))  # 이미 올린 PR


def test_approve_unknown_connection_rejected(service: PlanningService) -> None:
    _two()
    _req12_to_prs(service)
    with pytest.raises(ActionError) as err:
        run(service.approve_repo("PR #31", "nope"))
    assert err.value.code == "UNKNOWN_CONNECTION"
    assert _pr(service, "PR #31").target.status == "proposed"


def test_actions_api_approve_repo(service: PlanningService) -> None:
    _two()
    _req12_to_prs(service)
    c = TestClient(app)
    r = c.post("/api/planning/actions", json={"type": "approveRepo", "pr": "PR #31"})
    assert r.status_code == 200
    pr = next(p for p in r.json()["data"]["prs"] if p["id"] == "PR #31")
    assert pr["merged"] and pr["target"]["status"] == "approved"
    r = c.post("/api/planning/actions", json={"type": "rejectRepo", "pr": "PR #99"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NOT_FOUND"


def test_llm_judge_used_in_domain(service: PlanningService) -> None:
    _two()
    repo_routing.set_llm(
        MockLLMClient(responses={RepoPick: RepoPick(connection_id=API, reasons=["API 변경"])})
    )
    _req12_to_prs(service)
    t = _pr(service, "PR #31").target
    assert t.status == "proposed" and t.reasons[0] == "AI: API 변경"


def test_simple_request_waits_for_repo(service: PlanningService) -> None:
    _two()
    run(service.inbound("support_simple"))
    pr = [p for p in service.state.prs if p.auto][-1]
    assert pr.target.status == "proposed" and not pr.merged
    task = next(t for t in service.state.tasks if t.id == pr.task)
    assert task.state == "머지 대기"
    run(service.approve_repo(pr.id))
    pr = _pr(service, pr.id)
    assert pr.merged and pr.vcs_number is not None
    req = next(r for r in service.state.requests if r.spec and pr.task in {
        t.id for t in service.state.tasks if t.spec == r.spec
    })  # fmt: skip
    assert req.state == "완료"


def test_single_connection_auto_approved(service: PlanningService) -> None:
    vcs.upsert_connection(kind="mock", repo="acme/only")
    _req12_to_prs(service)
    pr31 = _pr(service, "PR #31")
    assert pr31.target.status == "approved" and pr31.target.decided_by == "자동 (연결 1개)"
    assert pr31.merged and _writes("conn-1") == [
        ("open_change", 1),
        ("open_change", 2),
        ("merge_change", 1),
    ]
    assert "vcs.repo_proposed" not in [a.point for a in service.state.activity]


def test_mock_default_unchanged() -> None:
    """연결이 없는 목 설정이면 제안·승인 없이 상태가 지금과 똑같다."""
    assert not vcs_bridge.enabled()
    a, b = PlanningService(), PlanningService()
    for svc in (a, b):
        set_service(svc)
        _req12_to_prs(svc)
    assert a.snapshot() == b.snapshot()
    assert all(p.target is None and p.vcs_number is None for p in a.state.prs)
    assert not any(x.point.startswith("vcs.") for x in a.state.activity)
    with pytest.raises(ActionError) as err:
        run(a.approve_repo("PR #31"))
    assert err.value.code == "NO_VCS"


def test_mount_prefixes_paths_for_safety() -> None:
    """레포를 회사 코드의 한 자리(앞머리)에 놓으면 관문용 경로에 그 앞머리가 붙는다."""
    from planning_agent import vcs

    vcs.set_vcs(None)
    vcs._screen = None
    try:
        c = vcs.upsert_connection(kind="mock", repo="acme/shop-web", mount="/shop-web")
        assert c.mount == "shop-web/" and c.public()["mount"] == "shop-web/"
        assert vcs.logical_paths(c.id, ["src/App.tsx", "shop-web/x.ts"]) == [
            "shop-web/src/App.tsx",
            "shop-web/x.ts",
        ]
        # 다시 저장할 때 앞머리를 안 주면 그대로 둔다
        assert vcs.upsert_connection(id=c.id, kind="mock", repo="acme/shop-web").mount == "shop-web/"
    finally:
        vcs._screen = None
        vcs.set_vcs(None)
