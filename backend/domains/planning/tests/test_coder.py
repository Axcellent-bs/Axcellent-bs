"""코딩 에이전트: 개발팀 기획 → 파일 고르기 → 쓰기 → PR → 리뷰 → 머지.

실제 GitHub·LLM은 부르지 않는다. 형상관리는 파일을 넣은 목 부품, LLM은 가짜 클라이언트다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError, LLMSettings
from planning_agent import coder, repo_routing, vcs
from planning_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.main import app
from planning_agent.vcs import MockVcsAdapter, VcsError
from planning_agent.vcs.base import CheckRun, Checks, combine_checks

BANNER = "shop-web/src/components/Banner.tsx"
NOTICE = "shop-web/src/components/Notice.tsx"
CHECKOUT = "shop-web/src/pages/Checkout.tsx"
BANNER_OLD = "export function Banner() {\n  return <div>배너</div>\n}\n"
BANNER_NEW = "export function Banner() {\n  return <div className='wrap'>신간 배너</div>\n}\n"
BANNER_FIX = "export function Banner() {\n  return <div className='wrap m'>신간 배너</div>\n}\n"
NOTICE_NEW = "export const NOTICE = '오늘 배송'\n"
FILES = {
    BANNER: BANNER_OLD,
    CHECKOUT: "export function Checkout() {\n  return null\n}\n",
    "shop-web/package-lock.json": "{}\n",
    "node_modules/react/index.js": "module.exports = {}\n",
    "shop-web/public/logo.png": "png",
    "shop-web/src/lib/notes.ts": "export const x = 1\n",
}
DONE = "배너에 신간 문구가 보인다\n모바일에서 잘리지 않는다"


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """스키마별로 정해 둔 답을 준다. 호출 기록(스키마 이름, 지시문, 데이터)을 남긴다."""

    def __init__(
        self,
        plan: CodePlan | None = None,
        writes: dict[str, str | Callable[[dict], str]] | None = None,
        reviews: list[CodeReview] | None = None,
        fail: set[str] | None = None,
    ):
        self.settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")
        self.plan = plan or CodePlan(
            files=[
                FilePick(path=BANNER, action="modify", reason="배너 문구 변경"),
                FilePick(path=NOTICE, action="create", reason="공지 상수 추가"),
            ],
            summary="배너 문구와 공지 추가",
        )
        self.writes = writes or {BANNER: BANNER_NEW, NOTICE: NOTICE_NEW}
        self.reviews = list(reviews or [CodeReview(verdict="통과", notes=[])])
        self.fail = fail or set()
        self.calls: list[tuple[str, str, dict]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        data = untrusted or {}
        self.calls.append((schema.__name__, prompt, data))
        if schema.__name__ in self.fail:
            raise LLMResponseError("LLM API가 500를 돌려줬다")
        if schema is CodePlan:
            return self.plan
        if schema is FileWrite:
            got = self.writes[data["path"]]
            content = got(data) if callable(got) else got
            return FileWrite(content=content, reason=f"{data['path']} 고침")
        if schema is CodeReview:
            return self.reviews.pop(0)
        raise KeyError(schema.__name__)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files(FILES)
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


async def _retry(svc: PlanningService, task_id: str):
    """다시 시도하고 같은 이벤트 루프에서 끝날 때까지 기다린다."""
    out = await svc.retry_dev_task(task_id)
    await svc.wait_coding()
    return out


async def _go(svc: PlanningService, **kw: Any):
    kw.setdefault("title", "신간 배너 문구 바꾸기")
    kw.setdefault("plan", "메인 배너 문구를 신간 안내로 바꾸고 오늘 배송 공지 상수를 추가한다.")
    kw.setdefault("done", DONE)
    out = await svc.create_dev_task(**kw)
    await svc.wait_coding()
    return out


def _task(svc: PlanningService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


def _points(svc: PlanningService) -> list[str]:
    return [a.point for a in svc.state.activity]


# ---------------------------------------------------------------- 흐름


def test_happy_path_opens_reviews_and_merges(service: PlanningService, adapter) -> None:
    fake = FakeLLM()
    coder.set_llm(fake)
    out = run(_go(service, paths=["shop-web/src/components"]))
    assert out.output == {
        "request_id": "REQ-D1",
        "spec_id": "SPEC-D1",
        "task_id": "T-D1",
        "status": "queued",
    }
    task = _task(service)
    c = task.coding
    assert c is not None and c.status == "merged" and c.error is None
    assert (c.model, c.attempts, c.connection, c.pr) == ("fake-coder", 1, "default", "PR #31")
    assert [(f.path, f.additions, f.deletions) for f in c.files] == [
        (BANNER, 1, 1),
        (NOTICE, 1, 0),
    ]
    assert c.summary == "배너 문구와 공지 추가"
    assert c.review is not None and c.review.verdict == "통과"
    assert task.state == "완료"
    # 요청·확정 기획서 (개발팀 기획)
    req = next(r for r in service.state.requests if r.id == "REQ-D1")
    sp = next(s for s in service.state.specs if s.id == "SPEC-D1")
    assert (req.from_, req.via, req.state) == ("dev", "dev_team", "완료")
    assert (sp.status, sp.origin, sp.handed_to_dev) == ("확정", "dev_team", True)
    assert [(p.id, p.kind) for p in sp.paras] == [("R1", "req"), ("C1", "done"), ("C2", "done")]
    assert sp.paras[0].paths == ["shop-web/src/components"]
    # PR: 진짜 파일로 올리고 자동 머지
    pr = next(p for p in service.state.prs if p.id == "PR #31")
    assert pr.merged and pr.vcs_number == 1 and pr.reviews == ["통과"]
    assert pr.changed_paths == [BANNER, NOTICE] and (pr.gate.lines, pr.gate.files) == (3, 2)
    assert pr.branch.startswith("gen/t-d1-") and pr.target.status == "approved"
    assert adapter.writes == [("open_change", 1), ("merge_change", 1)]
    assert adapter.contents(1) == {BANNER: BANNER_NEW, NOTICE: NOTICE_NEW}
    assert run(adapter.read_file(BANNER)) == BANNER_NEW  # 병합 뒤 기준 브랜치에 들어감
    body = adapter._entries[1].body
    assert "기획서: SPEC-D1" in body and "문단: SPEC-D1:R1" in body and "- C2: 모바일" in body
    # 관문: 작업 등록 → PR 생성 → 리뷰 → 머지
    actions = [g.action for g in service.gate_calls]
    for name in ("dev_task_create", "generate_pr", "review_pr", "merge_pr"):
        assert name in actions
    assert actions.index("generate_pr") < actions.index("review_pr") < actions.index("merge_pr")
    # 활동: 읽기·고르기·쓰기·리뷰
    texts = {a.point: a.text for a in service.state.activity if a.point.startswith("code.")}
    assert texts["code.read"] == "T-D1 파일 3개 읽음"
    assert texts["code.plan"] == "T-D1 고칠 파일 2개 선택"
    assert texts["code.write"] == "T-D1 2개 파일 · +2 −1줄"
    assert texts["code.review"] == "T-D1 리뷰 통과 · 노트 0개"
    assert "code.failed" not in texts
    # LLM에는 걸러 낸 목록만, 힌트 경로를 앞에 보낸다
    plan_data = fake.calls[0][2]
    assert plan_data["files"][0] == BANNER
    assert "node_modules/react/index.js" not in plan_data["files"]
    assert "shop-web/package-lock.json" not in plan_data["files"]
    assert "shop-web/public/logo.png" not in plan_data["files"]
    # 파일 글은 데이터로만 보낸다 (지시문에 섞지 않는다)
    write = next(x for x in fake.calls if x[0] == "FileWrite" and x[2]["path"] == BANNER)
    assert write[2]["current"] == BANNER_OLD and BANNER_OLD not in write[1]
    # SSE 상태에도 실린다
    dumped = next(t for t in service.snapshot()["tasks"] if t["id"] == "T-D1")
    assert dumped["coding"]["status"] == "merged" and dumped["coding"]["files"][0]["path"] == BANNER


def test_protected_path_waits_for_human_then_human_merges(
    service: PlanningService, adapter
) -> None:
    coder.set_llm(
        FakeLLM(
            plan=CodePlan(files=[FilePick(path=CHECKOUT, reason="결제 버튼 문구")]),
            writes={CHECKOUT: "export function Checkout() {\n  return <b>결제</b>\n}\n"},
        )
    )
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and "사람 확인" in (c.error or "")
    pr = next(p for p in service.state.prs if p.id == c.pr)
    assert not pr.merged and pr.auto_review is not None and pr.auto_review.state == "사람 확인"
    assert adapter.writes == [("open_change", 1)]
    run(service.merge_pr(pr.id))  # 사람 머지 버튼
    assert _task(service).coding.status == "merged"
    assert adapter.writes[-1] == ("merge_change", 1)


def test_too_many_files_waits_without_pr(service: PlanningService, adapter) -> None:
    picks = [FilePick(path=f"shop-web/src/new{i}.ts", action="create") for i in range(6)]
    coder.set_llm(FakeLLM(plan=CodePlan(files=picks)))
    before = len(service.state.prs)
    run(_go(service))
    task = _task(service)
    assert task.coding.status == "waiting_human" and task.state == "C 보류"
    assert "6개 > 5개" in task.coding.error and len(task.coding.files) == 6
    assert len(service.state.prs) == before and adapter.writes == []
    assert "generate_pr" not in [g.action for g in service.gate_calls]
    plan_line = next(a.text for a in service.state.activity if a.point == "code.plan")
    assert plan_line.endswith("한도 5개 초과, 사람 확인")


def test_too_many_lines_waits_without_pr(service: PlanningService, adapter) -> None:
    big = "".join(f"line {i}\n" for i in range(1600))
    coder.set_llm(
        FakeLLM(plan=CodePlan(files=[FilePick(path=NOTICE, action="create")]), writes={NOTICE: big})
    )
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and "1600줄 > 1500줄" in c.error
    assert c.files[0].additions == 1600 and adapter.writes == []


def test_review_mismatch_then_one_fix_then_pass(service: PlanningService, adapter) -> None:
    def banner(data: dict) -> str:
        return BANNER_FIX if data["review_notes"] else BANNER_NEW

    fake = FakeLLM(
        plan=CodePlan(files=[FilePick(path=BANNER, reason="배너")]),
        writes={BANNER: banner},
        reviews=[
            CodeReview(verdict="불일치", notes=["C2 모바일 문구가 없다"]),
            CodeReview(verdict="통과", notes=[]),
        ],
    )
    coder.set_llm(fake)
    run(_go(service))
    c = _task(service).coding
    assert c.status == "merged" and c.attempts == 2
    pr = next(p for p in service.state.prs if p.id == c.pr)
    assert pr.reviews == ["불일치", "통과"] and pr.merged
    # 리뷰·수정은 PR 전에 끝내고 마지막 결과만 커밋 하나로 올린다
    assert adapter.writes == [("open_change", 1), ("merge_change", 1)]
    assert adapter.contents(1) == {BANNER: BANNER_FIX}
    assert "code_write" not in [g.action for g in service.gate_calls]
    second = [x for x in fake.calls if x[0] == "FileWrite"][1]
    assert second[2]["review_notes"] == ["C2 모바일 문구가 없다"]
    assert second[2]["current"] == BANNER_NEW  # 지난 작성본 위에서 고친다
    assert _points(service).count("code.review") == 1


def test_review_mismatch_twice_waits_in_queue(service: PlanningService, adapter) -> None:
    coder.set_llm(
        FakeLLM(
            plan=CodePlan(files=[FilePick(path=BANNER)]),
            writes={BANNER: lambda d: BANNER_FIX if d["review_notes"] else BANNER_NEW},
            reviews=[CodeReview(verdict="불일치", notes=["a"]), CodeReview(verdict="불일치")],
        )
    )
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and c.attempts == 2
    pr = next(p for p in service.state.prs if p.id == c.pr)
    assert pr.reviews == ["불일치", "불일치"] and not pr.merged
    assert pr.auto_review is not None and "pr_review_mismatch" in pr.auto_review.rules
    rows = TestClient(app).get("/api/planning/review-queue").json()["data"]
    assert any(r["id"] == pr.id for r in rows)


def test_llm_failure_fails_then_retry_succeeds(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    c = _task(service).coding
    assert c.status == "failed" and c.error == "LLM 후보 고르기 실패 (LLMResponseError)"
    assert "code.failed" in _points(service) and adapter.writes == []
    assert "500" not in (c.error or "")

    async def retry():
        out = await service.retry_dev_task("T-D1")
        await service.wait_coding()
        return out

    coder.set_llm(FakeLLM())
    out = run(retry())
    assert out.output["status"] == "queued" and out.output["task_id"] == "T-D1"
    assert _task(service).coding.status == "merged"
    with pytest.raises(ActionError) as err:
        run(service.retry_dev_task("T-D1"))  # 끝난 작업
    assert err.value.code == "NOT_RETRYABLE"


def test_vcs_open_failure_fails_without_pr(service: PlanningService, adapter) -> None:
    class BrokenOpen(MockVcsAdapter):
        async def open_change(self, *a, **kw):
            raise VcsError("forbidden", "no access")

    broken = BrokenOpen()
    broken.seed_files(FILES)
    vcs.set_vcs(broken)
    coder.set_llm(FakeLLM())
    before = len(service.state.prs)
    run(_go(service))
    c = _task(service).coding
    assert c.status == "failed" and "forbidden" in c.error and c.pr is None
    assert len(service.state.prs) == before
    assert "vcs.open_failed" in _points(service)


def test_bad_paths_are_dropped(service: PlanningService, adapter) -> None:
    coder.set_llm(
        FakeLLM(
            plan=CodePlan(
                files=[
                    FilePick(path="../etc/passwd", action="create"),
                    FilePick(path="shop-web/missing.ts", action="modify"),
                    FilePick(path=f"./{BANNER}", action="modify"),
                    FilePick(path=BANNER, action="create"),  # 같은 경로 두 번
                ]
            )
        )
    )
    run(_go(service))
    c = _task(service).coding
    assert c.status == "merged" and [f.path for f in c.files] == [BANNER]
    line = next(a.text for a in service.state.activity if a.point == "code.plan")
    assert line == "T-D1 고칠 파일 1개 선택 (뺀 경로 3개)"


def test_reset_stops_running_job(service: PlanningService, adapter) -> None:
    class Slow(FakeLLM):
        async def structured(self, prompt, schema, **kw):
            service.reset()  # 도는 중에 초기화
            return await super().structured(prompt, schema, **kw)

    coder.set_llm(Slow())
    run(_go(service))
    assert not any(t.id == "T-D1" for t in service.state.tasks)
    assert adapter.writes == []


def test_unit_helpers() -> None:
    assert coder.diff_counts("a\nb\nc\n", "a\nB\nc\nd\n") == (2, 1)
    assert coder.diff_counts("", "x\n") == (1, 0)
    assert coder.clean_path("./a/b.ts") == "a/b.ts"
    assert coder.clean_path("a/../b") is None and coder.clean_path("/a") is None
    assert coder.area_of(["shop-web/src/a.ts", "shop-web/src/b/c.ts"]) == "shop-web/src/"
    assert coder.area_of(["shop-api/a.py", "shop-web/b.ts"]) == "shop-api, shop-web"
    assert coder.skip_path("dist/app.js") and coder.skip_path("a/yarn.lock")
    assert not coder.skip_path("src/app.ts")


# ---------------------------------------------------------------- REST


def test_api_validation_and_config_errors(service: PlanningService, adapter) -> None:
    c = TestClient(app)
    url = "/api/planning/dev-tasks"
    before = len(service.state.requests)
    coder.set_llm(FakeLLM())
    for body in (
        {},
        {"title": "t"},
        {"title": " ", "plan": "p"},
        {"title": "t", "plan": "x" * 8001},
        {"title": "t", "plan": "p", "paths": "shop-web/"},
        {"title": "t", "plan": "p", "done": 3},
        {"title": "t", "plan": "p", "connection": "nope"},
    ):
        r = c.post(url, json=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_INPUT", body
    # LLM 설정 없음 → 409, 아무것도 만들지 않는다
    coder.set_llm(None)
    r = c.post(url, json={"title": "t", "plan": "p"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    # 연결 없음 → 409
    vcs.set_vcs(None)
    coder.set_llm(FakeLLM())
    r = c.post(url, json={"title": "t", "plan": "p"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_VCS"
    # 연결이 여럿이면 하나를 골라야 한다
    vcs.upsert_connection(id="a", kind="mock", repo="acme/a")
    vcs.upsert_connection(id="b", kind="mock", repo="acme/b")
    r = c.post(url, json={"title": "t", "plan": "p"})
    assert r.status_code == 400 and "connection" in r.json()["error"]["message"]
    assert len(service.state.requests) == before
    r = c.post("/api/planning/dev-tasks/T-404/retry", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_api_creates_and_runs_in_background(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM())
    with TestClient(app) as c:
        r = c.post(
            "/api/planning/dev-tasks",
            json={"title": "신간 배너", "plan": "배너 문구 변경", "done": DONE, "paths": []},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data == {
            "request_id": "REQ-D1",
            "spec_id": "SPEC-D1",
            "task_id": "T-D1",
            "status": "queued",
        }
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = _task(service).coding.status
            if status in ("merged", "waiting_human", "failed"):
                break
            time.sleep(0.02)
        assert _task(service).coding.status == "merged"
        state = c.get("/api/planning/state").json()["data"]
        task = next(t for t in state["tasks"] if t["id"] == "T-D1")
        assert task["coding"]["review"]["verdict"] == "통과"
        r = c.post("/api/planning/dev-tasks/T-D1/retry")
        assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_RETRYABLE"


def test_planning_role_refuses(adapter) -> None:
    svc = PlanningService(role="planning")
    coder.set_llm(FakeLLM())
    with pytest.raises(ActionError) as err:
        run(svc.create_dev_task(title="t", plan="p"))
    assert err.value.code == "WRONG_ROLE"


def test_dev_role_reports_coding_result_to_planning(adapter) -> None:
    """나눠 띄우면 dev가 코딩 작업·PR을 receive_dev_result로 planning에 돌려준다."""
    sent: list[str] = []
    planning = PlanningService(role="planning")

    async def send(skill: str, ctx: str, tenant: str, payload, key: str) -> str:
        sent.append(skill)
        body = payload.model_dump(mode="json")
        await planning.receive_dev_result(
            context_id=ctx, requester="dev", idempotency_key=key, **body
        )
        return "보냄"

    dev = PlanningService(role="dev", dev_sender=send)
    coder.set_llm(FakeLLM())

    async def go() -> None:
        await dev.create_dev_task(title="신간 배너", plan="배너 문구 변경", done=DONE)
        await dev.wait_coding()
        await dev.wait_sent()

    run(go())
    assert _task(dev).coding.status == "merged"
    assert set(sent) == {"receive_dev_result"}
    got = _task(planning)
    assert got.coding is not None and got.coding.status == "merged"
    assert any(p.task == "T-D1" and p.merged for p in planning.state.prs)


def test_retry_reuses_written_code_without_llm(service: PlanningService, adapter) -> None:
    """PR을 못 올리고 멈춘 뒤 다시 시도하면 이미 작성한 코드로 PR만 다시 올린다.

    LLM은 다시 부르지 않는다.
    """

    class BrokenOpen(MockVcsAdapter):
        async def open_change(self, *a, **kw):
            raise VcsError("forbidden", "no access")

    broken = BrokenOpen()
    broken.seed_files(FILES)
    vcs.set_vcs(broken)
    llm = FakeLLM()
    coder.set_llm(llm)
    run(_go(service))
    assert _task(service).coding.status == "failed"
    wrote = [c[0] for c in llm.calls]
    assert wrote.count("CodePlan") == 2 and wrote.count("FileWrite") == 2  # 후보·확정

    vcs.set_vcs(adapter)  # 형상관리 권한을 고쳤다고 치고 다시 시도
    run(_retry(service, "T-D1"))
    again = [c[0] for c in llm.calls]
    assert again.count("CodePlan") == 2 and again.count("FileWrite") == 2  # 다시 쓰지 않음
    c = _task(service).coding
    assert c.pr is not None and c.status in ("opened", "merged")
    assert any("작성해 둔 코드로 PR 다시 시도" in a.text for a in service.state.activity)
    assert coder.draft_of(service, "T-D1") is None


def test_apply_edits_and_fallback() -> None:
    from planning_agent.coder import Edit, apply_edits

    assert apply_edits("a\nb\nc\n", [Edit(find="b\n", replace="B\n")]) == "a\nB\nc\n"
    assert apply_edits("x x", [Edit(find="x", replace="y")]) is None  # 두 번 나옴
    assert apply_edits("abc", [Edit(find="zz", replace="y")]) is None  # 없음


def test_edits_are_used_and_fix_only_flagged_files(service: PlanningService, adapter) -> None:
    """고칠 파일은 바꿀 부분(edits)만 받고, 리뷰가 짚은 파일만 다시 쓴다."""
    from planning_agent.coder import Edit

    class EditLLM(FakeLLM):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
            data = untrusted or {}
            if schema is FileWrite and data.get("action") == "modify":
                self.calls.append((schema.__name__, prompt, data))
                cur = data["current"]
                first = cur.splitlines(keepends=True)[0]
                edit = Edit(find=first, replace="// 고침\n" + first)
                return FileWrite(edits=[edit], reason="한 줄")
            return await super().structured(prompt, schema, untrusted, images, context_id)

    llm = EditLLM(
        reviews=[
            CodeReview(verdict="불일치", notes=["공지 보완"], fix_files=[NOTICE]),
            CodeReview(verdict="통과", notes=[]),
        ]
    )
    coder.set_llm(llm)
    run(_go(service))
    writes = [c[2]["path"] for c in llm.calls if c[0] == "FileWrite"]
    assert writes.count(BANNER) == 1 and writes.count(NOTICE) == 2  # 배너는 다시 안 씀
    banner = next(f for f in _task(service).coding.files if f.path == BANNER)
    assert banner.additions == 1 and banner.deletions == 0


def test_cancel_running_task_and_close_open_pr(service: PlanningService, adapter) -> None:
    """중단: 돌던 실행을 멈추고, 열린 PR은 닫는다. 머지된 작업은 중단하지 않는다."""
    # 1) 리뷰 중에 중단: 리뷰는 PR 전이라 PR 없이 멈춘다
    gate = asyncio.Event()

    class SlowReview(FakeLLM):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
            if schema is CodeReview:
                await gate.wait()
            return await super().structured(prompt, schema, untrusted, images, context_id)

    coder.set_llm(SlowReview())

    async def flow():
        await service.create_dev_task(
            title="배너", plan="배너 문구를 바꾼다", done=DONE, paths=None, connection=None
        )
        for _ in range(200):
            await asyncio.sleep(0.01)
            if _task(service).coding.status == "reviewing":
                break
        out = await service.cancel_dev_task("T-D1")
        gate.set()
        await service.wait_coding()
        return out

    out = run(flow())
    task = _task(service)
    assert out.outcome == "canceled" and task.coding.status == "canceled" and task.state == "중단"
    assert not any(p.task == "T-D1" for p in service.state.prs) and adapter.writes == []
    # 2) 이미 중단된 작업은 다시 중단하지 않는다
    with pytest.raises(ActionError):
        run(service.cancel_dev_task("T-D1"))

    # 3) 리뷰 불일치 두 번으로 PR이 열린 채 사람 확인 대기 → 중단하면 PR을 닫는다
    mismatch = CodeReview(verdict="불일치", notes=["아직 아님"])
    coder.set_llm(FakeLLM(reviews=[mismatch, mismatch]))
    run(_go(service))
    task = _task(service, "T-D2")
    assert task.coding.status == "waiting_human" and task.coding.pr
    run(service.cancel_dev_task("T-D2"))
    pr = next(p for p in service.state.prs if p.task == "T-D2")
    assert pr.closed and not pr.merged and ("close_change", pr.vcs_number) in adapter.writes
    assert "vcs.closed" in _points(service)

    # 4) 중단한 작업은 재개할 수 있다 (닫힌 PR은 막지 않는다, 처음부터 다시 돈다)
    coder.set_llm(FakeLLM())
    out = run(_resume(service, "T-D2"))
    assert out.outcome == "queued"
    task = _task(service, "T-D2")
    assert task.coding.status in ("merged", "waiting_human", "opened") and task.state != "중단"
    assert any("재개" in a.text for a in service.state.activity if a.point == "dev_task_retry")


async def _resume(service, task_id: str):
    out = await service.retry_dev_task(task_id)
    await service.wait_coding()
    return out


# ---------------------------------------------------------------- 파일 확정·리뷰 추가 파일


def test_confirm_step_sees_previews_and_can_change_picks(service: PlanningService, adapter) -> None:
    """이름으로 고른 후보의 내용을 보여 주고, 확정 단계가 고른 파일로 작업한다."""

    class TwoStepLLM(FakeLLM):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
            data = untrusted or {}
            if schema is CodePlan and "candidates" in data:
                self.calls.append(("Confirm", prompt, data))
                return CodePlan(files=[FilePick(path=BANNER, reason="배너 문구")], summary="확정")
            if schema is CodePlan:
                self.calls.append((schema.__name__, prompt, data))
                return CodePlan(
                    files=[
                        FilePick(path=BANNER, reason="배너"),
                        FilePick(path=CHECKOUT, reason="혹시 몰라"),
                    ]
                )
            return await super().structured(prompt, schema, untrusted, images, context_id)

    llm = TwoStepLLM(writes={BANNER: BANNER_NEW})
    coder.set_llm(llm)
    run(_go(service))
    confirm = next(c for c in llm.calls if c[0] == "Confirm")
    previews = {c["path"]: c["preview"] for c in confirm[2]["candidates"]}
    assert previews[BANNER] == BANNER_OLD and CHECKOUT in previews
    c = _task(service).coding
    assert c.status == "merged" and [f.path for f in c.files] == [BANNER]
    assert c.summary == "확정"
    write = next(x for x in llm.calls if x[0] == "FileWrite")
    assert write[2]["others"] == []


def test_review_can_add_a_file_in_fix_pass(service: PlanningService, adapter) -> None:
    """리뷰가 diff에 없던 파일을 짚으면 수정 단계에서 그 파일도 고친다 (없으면 새로 만든다)."""
    theme = "shop-web/src/theme.ts"
    NOTES = "shop-web/src/lib/notes.ts"
    fake = FakeLLM(
        plan=CodePlan(files=[FilePick(path=BANNER, reason="배너")]),
        writes={BANNER: BANNER_NEW, theme: "export const brand = 'blue'\n", NOTES: "x\n"},
        reviews=[
            CodeReview(verdict="불일치", notes=["테마 파일도 필요"], fix_files=[theme, NOTES]),
            CodeReview(verdict="통과", notes=[]),
        ],
    )
    coder.set_llm(fake)
    run(_go(service))
    c = _task(service).coding
    assert c.status == "merged" and c.attempts == 2, c.error
    assert {f.path for f in c.files} == {BANNER, theme, NOTES}
    wrote = [x[2] for x in fake.calls if x[0] == "FileWrite"]
    added = {d["path"]: d for d in wrote[1:]}
    assert added[theme]["action"] == "create" and added[theme]["current"] == ""
    assert added[NOTES]["action"] == "modify"
    assert added[NOTES]["current"] == FILES[NOTES]
    assert BANNER not in added  # 짚지 않은 파일은 다시 안 쓴다
    assert adapter.contents(1)[theme] == "export const brand = 'blue'\n"


def test_preview_keeps_outline_of_long_file() -> None:
    body = (
        ":root {\n  --brand: indigo;\n}\n"
        + "/* 채움 */\n" * 600
        + ".theme-shop {\n  --brand: green;\n}\n"
    )
    out = coder.preview_of(body)
    assert len(out) < len(body) and ":root {" in out
    assert ".theme-shop {" in out and "뼈대 줄" in out
    assert coder.preview_of("짧은 파일\n") == "짧은 파일\n"


def test_review_prompt_does_not_ask_for_build_evidence() -> None:
    assert "빌드" in coder.REVIEW_PROMPT and "볼 수 없" in coder.REVIEW_PROMPT
    assert "기획에 없는" in coder.WRITE_PROMPT


# ---------------------------------------------------------------- 대기 조치: 수정 지시·시간 초과


def test_cross_check_wait_escalates_to_human_after_timeout(
    service: PlanningService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    monkeypatch.setenv("PLANNING_CROSS_CHECK_TIMEOUT_MIN", "10")
    mismatch = CodeReview(verdict="불일치", notes=["아직 아님"])
    coder.set_llm(FakeLLM(reviews=[mismatch, mismatch]))
    run(_go(service))
    pr = next(p for p in service.state.prs if p.task == "T-D1")
    assert pr.auto_review is not None and pr.auto_review.state == "교차검증 대기"
    run(service.escalate_waits())  # 아직 10분 안 됨
    pr = next(p for p in service.state.prs if p.task == "T-D1")
    assert pr.auto_review.state == "교차검증 대기"
    pr.auto_review.at -= timedelta(minutes=11)
    run(service.escalate_waits())
    pr = next(p for p in service.state.prs if p.task == "T-D1")
    assert pr.auto_review.state == "사람 확인"
    assert "10분 응답 없음" in (_task(service).coding.error or "")


def test_revise_only_for_waiting_task_with_open_pr(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM())
    run(_go(service))  # 머지까지 감
    with pytest.raises(ActionError) as err:
        run(service.revise_dev_task("T-D1", "색을 바꿔"))
    assert err.value.code == "NOT_REVISABLE"
    with pytest.raises(ActionError) as err:
        run(service.revise_dev_task("T-D1", "   "))
    assert err.value.code == "INVALID_INPUT"


# ---------------------------------------------------------------- 작업 대기열 (같은 파일은 차례로)


def _two_writes():
    """첫 작업은 BANNER_NEW, 두 번째는 BANNER_FIX를 쓴다 (같은 파일을 차례로 고친다)."""
    n = {"i": 0}

    def write(_data):
        n["i"] += 1
        return BANNER_NEW if n["i"] == 1 else BANNER_FIX

    return {BANNER: write, NOTICE: NOTICE_NEW}


def _blocking_llm(gate: asyncio.Event):
    class FirstReviewWaits(FakeLLM):
        reviewed = 0

        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
            if schema is CodeReview and self.reviewed == 0:
                await gate.wait()
            if schema is CodeReview:
                self.reviewed += 1
            return await super().structured(prompt, schema, untrusted, images, context_id)

    return FirstReviewWaits(
        writes=_two_writes(), reviews=[CodeReview(verdict="통과", notes=[])] * 2
    )


async def _until(cond, tries: int = 300) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("기다린 상태가 오지 않음")


def test_same_file_task_waits_for_earlier_one(service, adapter, monkeypatch) -> None:
    """먼저 만든 작업이 같은 파일을 고치는 중이면 뒤 작업은 고칠 파일을 고른 뒤 기다리고,
    앞 작업이 끝나면 최신 코드로 이어서 쓴다 (덮어쓰지 않는다)."""
    monkeypatch.setattr(coder, "QUEUE_POLL_SECONDS", 0.01)
    gate = asyncio.Event()
    coder.set_llm(_blocking_llm(gate))

    async def flow():
        await service.create_dev_task(title="배너 1", plan="배너를 바꾼다", done=DONE)
        await _until(lambda: _task(service).coding.status == "reviewing")
        await service.create_dev_task(title="배너 2", plan="배너를 다시 바꾼다", done=DONE)
        await _until(lambda: _task(service, "T-D2").coding.status == "queued"
                     and _task(service, "T-D2").coding.waiting_for == ["T-D1"])  # fmt: skip
        second = _task(service, "T-D2").coding
        assert "같은 파일" in (second.error or "")
        gate.set()
        await service.wait_coding()

    run(flow())
    first, second = _task(service, "T-D1"), _task(service, "T-D2")
    assert first.coding.status == "merged" and second.coding.status == "merged"
    assert second.coding.waiting_for == [] and second.coding.error is None
    assert "code.queue" in _points(service)
    # 두 번째는 첫 번째가 머지된 뒤의 글 위에서 썼다 (덮어쓰지 않음)
    assert run(adapter.read_file(BANNER)) == BANNER_FIX


def test_now_does_not_wait(service, adapter, monkeypatch) -> None:
    """'지금 진행'(skip_queue)은 같은 파일 작업이 있어도 기다리지 않는다."""
    monkeypatch.setattr(coder, "QUEUE_POLL_SECONDS", 0.01)
    gate = asyncio.Event()
    coder.set_llm(_blocking_llm(gate))

    async def flow():
        await service.create_dev_task(title="배너 1", plan="배너를 바꾼다", done=DONE)
        await _until(lambda: _task(service).coding.status == "reviewing")
        await service.create_dev_task(
            title="배너 2", plan="배너를 다시 바꾼다", done=DONE, skip_queue=True
        )
        await _until(lambda: _task(service, "T-D2").coding.status in ("reviewing", "merged"))
        assert _task(service, "T-D2").coding.waiting_for == []
        gate.set()
        await service.wait_coding()

    run(flow())
    assert _task(service, "T-D2").coding.skip_queue


# ---------------------------------------------------------------- PR 직전 확인·CI·충돌 다시 쓰기


class ChecksAdapter(MockVcsAdapter):
    """PR CI 결과를 정해 둔다 (차례대로, 마지막 것은 계속)."""

    def __init__(self, *states: str):
        super().__init__()
        self.states = list(states)
        self.seed_files(FILES)

    async def get_checks(self, number: int) -> Checks:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == "none":
            return combine_checks([])
        return combine_checks([CheckRun(name="build", state=state)])  # type: ignore[arg-type]


def test_waits_for_ci_then_merges(service: PlanningService, adapter) -> None:
    vcs.set_vcs(ChecksAdapter("pending", "pending", "passed"))
    coder.set_llm(FakeLLM())
    run(_go(service))
    c = _task(service).coding
    assert c.status == "merged"
    texts = [a.text for a in service.state.activity if a.point == "code.ci"]
    assert any("CI 기다림" in t for t in texts) and any("CI 통과" in t for t in texts)


def test_ci_failure_holds_for_human(service: PlanningService, adapter) -> None:
    vcs.set_vcs(ChecksAdapter("failed"))
    coder.set_llm(FakeLLM())
    run(_go(service))
    c = _task(service).coding
    pr = next(p for p in service.state.prs if p.id == c.pr)
    assert c.status == "waiting_human" and "CI 실패 (build)" in (c.error or "")
    assert not pr.merged and pr.auto_review is not None


def test_ci_timeout_holds_for_human(service: PlanningService, adapter, monkeypatch) -> None:
    monkeypatch.setattr(coder, "CI_WAIT_SECONDS", 0.0)
    vcs.set_vcs(ChecksAdapter("pending"))
    coder.set_llm(FakeLLM())
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and "안 끝남" in (c.error or "")


def test_base_changed_before_pr_rewrites_once(service: PlanningService, adapter) -> None:
    """쓰는 동안 기준 브랜치의 같은 파일이 바뀌면 PR 직전 한 곳에서 최신 글 위에 다시 쓴다."""
    seen: list[str] = []

    def write_banner(data: dict) -> str:
        seen.append(data.get("current", data.get("content", "")))
        if len(seen) == 1:
            adapter.seed_files({BANNER: BANNER_OLD.replace("배너", "새 배너")})  # 사람이 먼저 머지
        return BANNER_NEW

    fake = FakeLLM(
        writes={BANNER: write_banner, NOTICE: NOTICE_NEW},
        reviews=[CodeReview(verdict="통과", notes=[]), CodeReview(verdict="통과", notes=[])],
    )
    coder.set_llm(fake)
    run(_go(service))
    c = _task(service).coding
    assert c.status == "merged"
    assert "code.rebase" in _points(service)
    assert [x[0] for x in fake.calls].count("CodeReview") == 2  # 다시 쓴 뒤 다시 리뷰


def test_merge_conflict_retry_closes_pr_and_rewrites(service: PlanningService, adapter) -> None:
    class Conflict(MockVcsAdapter):
        clash = True

        async def merge_change(self, number: int, method: str = "squash"):
            if self.clash:
                raise VcsError("conflict", "merge conflict")
            return await super().merge_change(number, method)

    broken = Conflict()
    broken.seed_files(FILES)
    vcs.set_vcs(broken)
    coder.set_llm(FakeLLM(reviews=[CodeReview(verdict="통과", notes=[])] * 2))
    run(_go(service))
    c = _task(service).coding
    first = c.pr
    assert c.status == "failed" and c.merge_conflict and "충돌" in (c.error or "")

    broken.clash = False  # 최신 코드로 다시 쓰면 충돌이 풀린다
    run(_retry(service, "T-D1"))
    old = next(p for p in service.state.prs if p.id == first)
    c = _task(service).coding
    assert old.closed and c.pr != first and c.status == "merged"
