"""개발(dev) 쪽 auto_confirm_merge 시험 (분리 5단계: 기획 시험에서 나눔)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_workflow_testing import feed, plan
from dev_agent.main import app
from dev_agent.runtime import get_service, set_service
from dev_agent.service import DevService
from planning_agent.domain.autocheck import (
    IRREVERSIBLE_PREFIXES,
    PR_ALLOWED_PATHS,
    merge_risk_paths,
    pr_check,
    pr_params,
)
from planning_agent.domain.service import PlanningService

ANSWER = "품절 상품도 남겨 두고 품절 표시해 주세요"


PAYMENT_MIGRATION = next(p for p in IRREVERSIBLE_PREFIXES if "migrations" in p) + "0042_drop.py"


PR_KEYS = {
    "changed_lines",
    "changed_files",
    "changed_paths",
    "review_mismatch_count",
    "linked_incident_count",
    "irreversible_count",
}


def run(coro):
    return asyncio.run(coro)


class CaptureGate:
    """execute 호출을 params까지 기록한다. `hold`에 넣은 액션은 safety C 보류로 돌려준다."""

    def __init__(self, hold: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hold = hold

    async def __call__(self, action, params, meta, evidence) -> ExecuteDecision:
        self.calls.append((action, params))
        if action in self.hold:
            return ExecuteDecision(
                execution_id="exe_hold",
                grade="C",
                status="held",
                hold_reason="over_threshold",
                review_id="rev_7",
            )
        return ExecuteDecision(execution_id=f"exe_{len(self.calls)}", grade="A", status="executed")

    def params(self, action: str) -> list[dict]:
        return [p for a, p in self.calls if a == action]


@pytest.fixture
def gate(request: pytest.FixtureRequest) -> CaptureGate:
    """기록용 관문을 끼운 서비스 (시험의 역할 표시대로 기획·개발 서비스)."""
    capture = CaptureGate()
    mark = request.node.get_closest_marker("role")
    cls = DevService if mark and mark.args[0] == "dev" else PlanningService
    set_service(cls(gate=capture))
    return capture


def _svc() -> PlanningService:
    return get_service()


def _spec(svc: PlanningService, spec_id: str = "SPEC-7"):
    return next(s for s in svc.state.specs if s.id == spec_id)


def _pr(svc: PlanningService, pr_id: str):
    return next(p for p in svc.state.prs if p.id == pr_id)


@pytest.mark.role("dev")
def test_pr_params_each_key(service) -> None:
    pr28 = _pr(service, "PR #28")
    # PR #28: SPEC-5 R3(장애 표시 ctx_inc_0001)에 연결된 머지 PR
    assert pr_params(service.state, pr28) == {
        "changed_lines": 14,
        "changed_files": 1,
        "changed_paths": ["shop-api/payment/config.py"],
        "review_mismatch_count": 0,
        "linked_incident_count": 1,
        "irreversible_count": 0,
    }
    assert pr_check(service.state, pr28).rules == ["pr_linked_incident"]

    other = pr28.model_copy(
        update={
            "id": "PR #99",
            "number": 99,
            "task": "T-none",
            "reviews": ["불일치", "불일치", "통과"],
            "gate": pr28.gate.model_copy(update={"lines": 301, "files": 21}),
            "changed_paths": ["infra/x.yml", PAYMENT_MIGRATION, "shop-api/payment/config.py"],
        }
    )
    service.state.prs.append(other)
    params = pr_params(service.state, other)
    assert params["changed_paths"] == sorted(other.changed_paths)
    assert (params["changed_lines"], params["changed_files"]) == (301, 21)
    assert params["review_mismatch_count"] == 2
    # 자기 문단은 없지만 같은 파일을 바꾼 PR #28의 문단(SPEC-5 R3)이 장애 표시
    assert params["linked_incident_count"] == 1
    assert params["irreversible_count"] == 1
    assert pr_check(service.state, other).rules == [
        "pr_changed_lines",
        "pr_changed_files",
        "pr_allowed_paths",
        "pr_review_mismatch",
        "pr_linked_incident",
        "irreversible",
    ]


@pytest.mark.role("dev")
def test_human_merge_sends_171_keys(gate) -> None:
    svc = _svc()
    gate.hold = ("merge_pr",)  # 자동 머지는 safety가 보류 → 사람 머지
    plan(svc, "confirm_spec", "SPEC-7")
    run(svc.generate_prs("SPEC-7"))
    gate.hold = ()
    run(svc.merge_pr("PR #31"))
    auto_try, human = gate.params("merge_pr")
    assert auto_try["auto"] is True and human["auto"] is False
    for params in (auto_try, human):
        assert PR_KEYS <= set(params)
        assert params["changed_paths"] == ["shop-api/cart/service.py"]
        assert (params["changed_lines"], params["changed_files"]) == (86, 3)
        assert params["review_mismatch_count"] == 0
        assert params["linked_incident_count"] == 0 and params["irreversible_count"] == 0
    assert _pr(svc, "PR #31").merged and _pr(svc, "PR #31").auto is False


@pytest.mark.role("dev")
def test_rule_hit_waits_for_cross_check(gate) -> None:
    svc = _svc()
    # 확정·넘기기는 기획 서비스 복사본 기준 (허용 경로(shop-api·shop-web·docs) 밖)
    _spec(feed(svc).planning).paras[1].paths = ["infra/ci/deploy.yml"]
    plan(svc, "confirm_spec", "SPEC-7")
    run(svc.review_pr("PR #32"))
    pr32 = _pr(svc, "PR #32")
    assert not pr32.merged
    assert (pr32.auto_review.state, pr32.auto_review.rules) == (
        "교차검증 대기",
        ["pr_allowed_paths"],
    )
    assert pr32.auto_review.params["changed_paths"] == ["infra/ci/deploy.yml"]
    assert [m["auto"] for m in gate.params("merge_pr")] == [True]  # PR #31만, #32는 안 부름


@pytest.mark.role("dev")
def test_shop_web_is_allowed_but_checkout_needs_human(gate) -> None:
    """#200: shop-web/은 허용 경로지만 결제 화면(shop-web/checkout/)은 irreversible_count로 센다."""
    assert "shop-web/" in PR_ALLOWED_PATHS
    svc = _svc()
    plan(svc, "confirm_spec", "SPEC-7")
    run(svc.generate_prs("SPEC-7"))
    run(svc.review_pr("PR #32"))
    pr32 = _pr(svc, "PR #32")
    assert not pr32.merged
    assert (pr32.auto_review.state, pr32.auto_review.rules) == ("사람 확인", ["irreversible"])
    assert pr32.auto_review.params["changed_paths"] == ["shop-web/checkout/FailNotice.tsx"]
    assert pr32.auto_review.params["irreversible_count"] == 1
    assert [m["auto"] for m in gate.params("merge_pr")] == [
        True
    ]  # 위험 경로는 자동 머지 시도 안 함
    # 기획서 확정은 코드를 바꾸지 않으므로 화면 위험 경로를 세지 않는다
    assert gate.params("confirm_spec")[0]["irreversible_count"] == 0
    run(svc.merge_pr("PR #32"))  # 판단은 사람 머지 버튼
    merge = gate.params("merge_pr")[-1]
    assert merge["irreversible_count"] == 1 and merge["auto"] is False
    assert _pr(svc, "PR #32").merged


@pytest.mark.role("dev")
def test_safety_protected_paths_count_as_merge_risk() -> None:
    """safety #203 보호 경로는 planning도 머지 위험으로 센다 (자동 머지를 시도하지 않는다)."""
    protected = [
        "shop-web/src/pages/Checkout.tsx",
        "shop-web/src/lib/auth.tsx",
        "shop-api/src/ax_shop_api/service.py",
        ".github/workflows/ci.yml",
        "contracts/src/ax_contracts/schemas/planning.py",
    ]
    assert merge_risk_paths(protected) == protected
    assert merge_risk_paths(["shop-web/src/pages/Notice.tsx", "docs/a.md"]) == []


def test_agent_config_and_hook_files_are_merge_risk_at_any_depth() -> None:
    """AI PR이 에이전트 지시·훅·설치 스크립트를 바꾸면 자동 머지하지 않는다 (AGENTS.md·훅 주입)."""
    risky = [
        ".claude/settings.json",
        "shop-web/.claude/hooks/start.sh",
        "shop-web/CLAUDE.md",
        "docs/AGENTS.md",
        ".mcp.json",
        "shop-web/package.json",
        "shop-api/pyproject.toml",
        ".vscode/tasks.json",
        ".github/workflows/ci.yml",
        "shop-api/tests/conftest.py",
        ".cursor/rules/a.mdc",
        ".kiro/steering/x.md",
        "shop-web/.envrc",
        ".clinerules",
    ]
    assert merge_risk_paths(risky) == risky
    plain = ["shop-web/src/pages/Notice.tsx", "docs/claude-notes.md", "shop-web/package-lock.json"]
    assert merge_risk_paths(plain) == []


@pytest.mark.role("dev")
def test_plain_shop_web_path_passes_allowed_paths(service) -> None:
    pr = _pr(service, "PR #28").model_copy(
        update={
            "id": "PR #98",
            "number": 98,
            "task": "T-none",
            "changed_paths": ["shop-web/pages/notice.tsx"],
        }
    )
    service.state.prs.append(pr)
    check = pr_check(service.state, pr)
    assert "pr_allowed_paths" not in check.rules and check.params["irreversible_count"] == 0


@pytest.mark.role("dev")
def test_linked_incident_and_review_mismatch_wait(gate) -> None:
    svc = _svc()
    # 확정·넘기기는 기획 서비스 복사본 기준 (PR #28(장애 연결)과 같은 파일)
    _spec(feed(svc).planning).paras[0].paths = ["shop-api/payment/config.py"]
    plan(svc, "confirm_spec", "SPEC-7")
    pr31 = _pr(svc, "PR #31")
    assert not pr31.merged
    assert pr31.auto_review.rules == ["pr_linked_incident"]
    assert pr31.auto_review.params["linked_incident_count"] == 1
    # 검수 불일치 2회
    svc.state.prs[-1].reviews = ["불일치", "불일치"]
    run(svc.review_pr("PR #32"))
    assert "pr_review_mismatch" in _pr(svc, "PR #32").auto_review.rules


@pytest.mark.role("dev")
def test_irreversible_needs_human_and_human_can_merge(gate) -> None:
    svc = _svc()
    _spec(feed(svc).planning).paras[0].paths = [PAYMENT_MIGRATION]
    plan(svc, "confirm_spec", "SPEC-7")
    pr31 = _pr(svc, "PR #31")
    assert not pr31.merged
    assert pr31.auto_review.state == "사람 확인"
    assert "irreversible" in pr31.auto_review.rules
    assert gate.params("merge_pr") == []  # 되돌릴 수 없는 영역은 자동 머지를 시도하지 않는다
    run(svc.merge_pr("PR #31"))  # 사람 판단
    [merge] = gate.params("merge_pr")
    assert merge["irreversible_count"] == 1 and merge["auto"] is False
    pr31 = _pr(svc, "PR #31")
    assert pr31.merged and pr31.auto_review is None and not pr31.auto


@pytest.mark.role("dev")
def test_review_queue_drops_closed_pr(service) -> None:
    """레포에서 닫히거나 작업을 중단해 닫힌 PR은 사람 확인 대기에 남지 않는다."""
    client = TestClient(app)
    plan(service, "answer_question", "SPEC-7", ANSWER)
    plan(service, "confirm_spec", "SPEC-7")
    run(service.generate_prs("SPEC-7"))
    run(service.review_pr("PR #32"))
    assert [r["id"] for r in client.get("/api/planning/review-queue").json()["data"]] == ["PR #32"]
    _pr(service, "PR #32").closed = True
    assert client.get("/api/planning/review-queue").json()["data"] == []
