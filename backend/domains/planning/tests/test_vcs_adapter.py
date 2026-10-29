"""형상관리 연결 계층 인터페이스 테스트 (FR-AG-011, NFR-EXT-003).

같은 계약 테스트 묶음을 목 부품과 GitHub 부품(가짜 서버 `httpx.MockTransport`)에 똑같이 돌린다.
실제 GitHub는 부르지 않는다. 토큰은 가짜 값이다.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from planning_agent import vcs, vcs_bridge
from planning_agent.domain.service import PlanningService
from planning_agent.runtime import set_service
from planning_agent.vcs import CheckRun, MockVcsAdapter, VcsError, parse_trace
from planning_agent.vcs.github import GithubVcsAdapter, added_lines

FAKE_TOKEN = "fake-token-0123456789"
REPO = "acme/shop"
T0 = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _no_override():
    vcs.set_vcs(None)
    yield
    vcs.set_vcs(None)


# ---------------------------------------------------------------- 가짜 GitHub 서버


@dataclass
class FakeGithub:
    """REST v3에서 부품이 쓰는 경로만 흉내 낸다."""

    token: str = FAKE_TOKEN
    base_branch: str = "dev"
    prefix: str = f"/repos/{REPO}"
    clock: Callable[[], datetime] = lambda: T0
    refs: dict[str, str] = field(default_factory=dict)
    commits: dict[str, dict[str, str]] = field(default_factory=dict)  # sha → {경로: 내용}
    blobs: dict[str, str] = field(default_factory=dict)
    trees: dict[str, dict[str, str]] = field(default_factory=dict)
    pulls: dict[int, dict] = field(default_factory=dict)
    files: dict[int, list[dict]] = field(default_factory=dict)
    check_runs: dict[str, list[dict]] = field(default_factory=dict)
    statuses: dict[str, list[dict]] = field(default_factory=dict)
    fail: dict[tuple[str, str], httpx.Response] = field(default_factory=dict)
    seen: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._seq = itertools.count(1)
        root = self._sha("root")
        self.commits[root] = {"README.md": "shop\n"}
        self.refs[self.base_branch] = root

    def _sha(self, seed: str) -> str:
        return hashlib.sha1(f"{seed}{next(self._seq)}".encode()).hexdigest()

    # ---- 시드

    def seed(self, *, title, body="", branch, files, merged_at, base=None, runs=None) -> int:
        number = len(self.pulls) + 1
        head = self._sha("head")
        self.pulls[number] = {
            "number": number,
            "title": title,
            "body": body,
            "head": {"ref": branch, "sha": head},
            "base": {"ref": base or self.base_branch},
            "merged": merged_at is not None,
            "merged_at": _iso(merged_at) if merged_at else None,
            "updated_at": _iso(merged_at or T0),
            "state": "closed" if merged_at else "open",
            "html_url": f"https://github.example/{REPO}/pull/{number}",
        }
        self.files[number] = [
            {"filename": p, "additions": a, "deletions": d, "patch": None}
            for p, (a, d) in files.items()
        ]
        self._set_runs(head, runs if runs is not None else [CheckRun("ci", "passed")])
        return number

    def _set_runs(self, sha: str, runs: list[CheckRun]) -> None:
        raw = {
            "passed": ("completed", "success"),
            "failed": ("completed", "failure"),
            "pending": ("in_progress", None),
        }
        self.check_runs[sha] = [
            {"name": r.name, "status": raw[r.state][0], "conclusion": raw[r.state][1]} for r in runs
        ]
        self.statuses[sha] = []

    def set_checks(self, number: int, runs: list[CheckRun]) -> None:
        self._set_runs(self.pulls[number]["head"]["sha"], runs)

    def seed_files(self, files: dict[str, str]) -> None:
        """기준 브랜치 파일을 넣는다."""
        self.commits[self.refs[self.base_branch]].update(files)

    def _pull_files(self, head: str, base: str) -> list[dict]:
        new, old = self.commits[head], self.commits[self.refs[base]]
        out = []
        for path, content in new.items():
            if old.get(path) == content:
                continue
            rows = content.splitlines()
            patch = f"@@ -0,0 +1,{len(rows)} @@\n" + "\n".join(f"+{r}" for r in rows)
            out.append({"filename": path, "additions": len(rows), "deletions": 0, "patch": patch})
        return out

    # ---- 요청 처리

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.path
        assert path.startswith(self.prefix), path
        path = path[len(self.prefix) :]
        for (method, pattern), res in self.fail.items():
            if request.method == method and re.fullmatch(pattern, path):
                return res
        body = json.loads(request.content) if request.content else {}
        q = request.url.params
        m = request.method

        if m == "GET" and (g := re.fullmatch(r"/git/ref/heads/(.+)", path)):
            sha = self.refs.get(g.group(1))
            if sha is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": sha}})
        if m == "GET" and (g := re.fullmatch(r"/git/commits/(\w+)", path)):
            return httpx.Response(200, json={"sha": g.group(1), "tree": {"sha": f"t{g.group(1)}"}})
        if m == "POST" and path == "/git/blobs":
            sha = self._sha("blob")
            self.blobs[sha] = body["content"]
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and path == "/git/trees":
            base = self.commits[body["base_tree"][1:]]
            tree = {**base, **{i["path"]: self.blobs[i["sha"]] for i in body["tree"]}}
            sha = self._sha("tree")
            self.trees[sha] = tree
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and path == "/git/commits":
            sha = self._sha("commit")
            self.commits[sha] = self.trees[body["tree"]]
            self.commits[sha + ":parent"] = self.commits[body["parents"][0]]
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and path == "/git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[name] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})
        if m == "PATCH" and (g := re.fullmatch(r"/git/refs/heads/(.+)", path)):
            name = g.group(1)
            if name not in self.refs:
                return httpx.Response(422, json={"message": "Reference does not exist"})
            self.refs[name] = body["sha"]
            for number, pr in self.pulls.items():
                if pr["head"]["ref"] == name and not pr["merged"]:
                    pr["head"]["sha"] = body["sha"]
                    self.files[number] = self._pull_files(body["sha"], pr["base"]["ref"])
            return httpx.Response(200, json={"ref": f"refs/heads/{name}"})
        if m == "GET" and (g := re.fullmatch(r"/git/trees/(\w+)", path)):
            tree = self.commits.get(g.group(1))
            if tree is None or q.get("recursive") != "1":
                return httpx.Response(404, json={"message": "Not Found"})
            rows = [{"path": p, "type": "blob", "size": len(c)} for p, c in tree.items()]
            rows.append({"path": "shop-web", "type": "tree"})
            return httpx.Response(200, json={"tree": rows, "truncated": False})
        if m == "GET" and (g := re.fullmatch(r"/contents/(.+)", path)):
            sha = self.refs.get(q.get("ref") or self.base_branch)
            content = self.commits.get(sha or "", {}).get(g.group(1))
            if content is None:
                return httpx.Response(404, json={"message": "Not Found"})
            raw = content.encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            # GitHub처럼 60자마다 줄을 바꿔 보낸다
            wrapped = "\n".join(encoded[i : i + 60] for i in range(0, len(encoded), 60))
            return httpx.Response(
                200,
                json={"type": "file", "encoding": "base64", "size": len(raw), "content": wrapped},
            )
        if m == "POST" and path == "/pulls":
            return self._open(body)
        if m == "GET" and path == "/pulls":
            rows = [
                p
                for p in self.pulls.values()
                if p["state"] == q.get("state") and p["base"]["ref"] == q.get("base")
            ]
            rows.sort(key=lambda p: p["updated_at"], reverse=True)
            page, per = int(q.get("page", 1)), int(q.get("per_page", 30))
            return httpx.Response(200, json=rows[(page - 1) * per : page * per])
        if g := re.fullmatch(r"/pulls/(\d+)(/files|/merge)?", path):
            number = int(g.group(1))
            pr = self.pulls.get(number)
            if pr is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if m == "GET" and g.group(2) is None:
                return httpx.Response(200, json=pr)
            if m == "GET" and g.group(2) == "/files":
                page, per = int(q.get("page", 1)), int(q.get("per_page", 30))
                return httpx.Response(200, json=self.files[number][(page - 1) * per : page * per])
            if m == "PUT" and g.group(2) == "/merge":
                if pr["merged"]:
                    return httpx.Response(405, json={"message": "Pull Request is not mergeable"})
                now = _iso(self.clock())
                pr.update(merged=True, merged_at=now, updated_at=now, state="closed")
                return httpx.Response(200, json={"merged": True, "sha": self._sha("merge")})
        if m == "GET" and (g := re.fullmatch(r"/commits/(\w+)/check-runs", path)):
            runs = self.check_runs.get(g.group(1), [])
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if m == "GET" and (g := re.fullmatch(r"/commits/(\w+)/status", path)):
            return httpx.Response(200, json={"statuses": self.statuses.get(g.group(1), [])})
        return httpx.Response(404, json={"message": f"no route {m} {path}"})

    def _open(self, body: dict) -> httpx.Response:
        head = self.refs.get(body["head"])
        if head is None:
            return httpx.Response(422, json={"message": "Validation Failed"})
        number = len(self.pulls) + 1
        new, old = self.commits[head], self.commits[head + ":parent"]
        files = []
        for path, content in new.items():
            if old.get(path) == content:
                continue
            rows = content.splitlines()
            patch = f"@@ -0,0 +1,{len(rows)} @@\n" + "\n".join(f"+{r}" for r in rows)
            files.append({"filename": path, "additions": len(rows), "deletions": 0, "patch": patch})
        now = _iso(self.clock())
        self.pulls[number] = {
            "number": number,
            "title": body["title"],
            "body": body["body"],
            "head": {"ref": body["head"], "sha": head},
            "base": {"ref": body["base"]},
            "merged": False,
            "merged_at": None,
            "updated_at": now,
            "state": "open",
            "html_url": f"https://github.example/{REPO}/pull/{number}",
        }
        self.files[number] = files
        self._set_runs(head, [CheckRun("ci", "passed")])
        return httpx.Response(201, json=self.pulls[number])


# ---------------------------------------------------------------- 부품 묶음


@dataclass
class Harness:
    """계약 테스트가 부품마다 다르게 해야 하는 일(시드·시계·CI 바꾸기)만 모은다."""

    name: str
    adapter: Any
    seed: Callable[..., int]
    set_checks: Callable[[int, list[CheckRun]], None]
    tick: Callable[[datetime], None]


def _mock_harness() -> Harness:
    now = {"t": T0}
    adapter = MockVcsAdapter(clock=lambda: now["t"])

    def seed(*, title, body="", branch, files, merged_at, base=None, runs=None) -> int:
        return adapter.seed(
            title=title,
            body=body,
            branch=branch,
            files=files,
            merged_at=merged_at,
            base=base,
            checks=runs,
        )

    return Harness("mock", adapter, seed, adapter.set_checks, lambda t: now.__setitem__("t", t))


def _github_harness(base_url: str = "https://api.github.com") -> Harness:
    now = {"t": T0}
    prefix = "/api/v3" if base_url.endswith("/api/v3") else ""
    server = FakeGithub(clock=lambda: now["t"], prefix=f"{prefix}/repos/{REPO}")
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    adapter = GithubVcsAdapter(REPO, FAKE_TOKEN, base_url=base_url, client=client)
    h = Harness(
        "github", adapter, server.seed, server.set_checks, lambda t: now.__setitem__("t", t)
    )
    h.server = server  # type: ignore[attr-defined]
    return h


@pytest.fixture(params=["mock", "github", "github-enterprise"])
def h(request) -> Harness:
    if request.param == "mock":
        return _mock_harness()
    if request.param == "github":
        return _github_harness()
    return _github_harness("https://ghe.example.com/api/v3")


BODY = "요청: REQ-12\n기획서: SPEC-7\n문단: SPEC-7:R1\n작업: T-11\n완료 기준 C1 확인"
FILES = {"shop-api/cart/service.py": "def keep():\n    return True\n"}


# ---------------------------------------------------------------- 같은 계약 테스트 묶음


def test_capabilities_writeable(h: Harness) -> None:
    caps = h.adapter.capabilities
    assert caps.kind == ("mock" if h.name == "mock" else "github")
    assert caps.read_only is False
    assert isinstance(h.adapter, vcs.VcsAdapter)


def test_open_change_then_get_change(h: Harness) -> None:
    opened = run(h.adapter.open_change("gen/t-11", "R1: 장바구니 보존", BODY, files=FILES))
    assert opened.branch == "gen/t-11" and opened.number >= 1 and opened.url
    change = run(h.adapter.get_change(opened.number))
    assert (change.title, change.branch, change.base, change.merged) == (
        "R1: 장바구니 보존",
        "gen/t-11",
        "dev",
        False,
    )
    assert change.changed_paths == ["shop-api/cart/service.py"]
    assert (change.additions, change.deletions) == (2, 0)
    assert change.files[0].lines == (1, 2)


@pytest.mark.parametrize("branch", ["feature/x", "gen/", "main", "gen/../dev"])
def test_branch_must_start_with_gen(h: Harness, branch: str) -> None:
    with pytest.raises(VcsError) as err:
        run(h.adapter.open_change(branch, "t", BODY, files=FILES))
    assert err.value.code == "invalid"
    if h.name != "mock":
        # 접두사가 틀리면 서버에 아무것도 보내지 않는다
        assert h.server.seen == []  # type: ignore[attr-defined]


def test_open_without_files_is_invalid(h: Harness) -> None:
    with pytest.raises(VcsError) as err:
        run(h.adapter.open_change("gen/t-1", "t", BODY, files={}))
    assert err.value.code == "invalid"


def test_same_branch_twice_is_conflict(h: Harness) -> None:
    run(h.adapter.open_change("gen/t-11", "t", BODY, files=FILES))
    with pytest.raises(VcsError) as err:
        run(h.adapter.open_change("gen/t-11", "t", BODY, files=FILES))
    assert err.value.code == "conflict"


def test_trace_meta_reads_ids(h: Harness) -> None:
    opened = run(h.adapter.open_change("gen/t-11", "R1: 장바구니 보존", BODY, files=FILES))
    meta = run(h.adapter.get_trace_meta(opened.number))
    assert meta.number == opened.number
    assert meta.requests == ("REQ-12",)
    assert meta.specs == ("SPEC-7",)
    assert meta.paras == ("SPEC-7:R1", "C1")


def test_merge_then_found_in_window(h: Harness) -> None:
    opened = run(h.adapter.open_change("gen/t-11", "R1", BODY, files=FILES))
    h.tick(T0 + timedelta(minutes=5))
    merged = run(h.adapter.merge_change(opened.number))
    assert merged.merged and merged.merged_at == T0 + timedelta(minutes=5)
    assert run(h.adapter.get_change(opened.number)).merged
    with pytest.raises(VcsError) as err:
        run(h.adapter.merge_change(opened.number))
    assert err.value.code == "conflict"
    found = run(h.adapter.find_changes(T0, T0 + timedelta(hours=1)))
    assert [c.number for c in found] == [opened.number]
    assert run(h.adapter.find_changes(T0, T0 + timedelta(hours=1), "shop-web/")) == []


def test_find_changes_window_prefix_and_order(h: Harness) -> None:
    a = h.seed(
        title="R1 결제",
        body="SPEC-5:R3",
        branch="gen/a",
        files={"shop-api/payment/x.py": (3, 1)},
        merged_at=T0 + timedelta(minutes=10),
    )
    b = h.seed(
        title="R2 검색",
        branch="gen/b",
        files={"shop-web/search/y.tsx": (1, 0)},
        merged_at=T0 + timedelta(minutes=20),
    )
    h.seed(  # 구간 밖
        title="옛 PR",
        branch="gen/c",
        files={"shop-api/payment/z.py": (1, 0)},
        merged_at=T0 - timedelta(days=2),
    )
    h.seed(title="열린 PR", branch="gen/d", files={"shop-api/payment/w.py": (1, 0)}, merged_at=None)
    h.seed(  # 다른 브랜치로 병합
        title="main",
        branch="gen/e",
        files={"shop-api/payment/v.py": (1, 0)},
        merged_at=T0 + timedelta(minutes=15),
        base="main",
    )
    window = (T0, T0 + timedelta(hours=1))
    assert [c.number for c in run(h.adapter.find_changes(*window))] == [b, a]
    pay = run(h.adapter.find_changes(*window, path_prefix="shop-api/payment"))
    assert [c.number for c in pay] == [a]
    assert (pay[0].additions, pay[0].deletions) == (3, 1)


def test_unknown_change_is_not_found(h: Harness) -> None:
    for call in (h.adapter.get_change, h.adapter.get_trace_meta, h.adapter.get_checks):
        with pytest.raises(VcsError) as err:
            run(call(999))
        assert err.value.code == "not_found"


def test_bad_merge_method_is_invalid(h: Harness) -> None:
    opened = run(h.adapter.open_change("gen/t-1", "t", BODY, files=FILES))
    with pytest.raises(VcsError) as err:
        run(h.adapter.merge_change(opened.number, method="octopus"))
    assert err.value.code == "invalid"
    assert not run(h.adapter.get_change(opened.number)).merged


@pytest.mark.parametrize(
    ("runs", "state"),
    [
        ([CheckRun("ci", "passed"), CheckRun("lint", "passed")], "passed"),
        ([CheckRun("ci", "passed"), CheckRun("lint", "failed")], "failed"),
        ([CheckRun("ci", "passed"), CheckRun("e2e", "pending")], "pending"),
        ([CheckRun("ci", "failed"), CheckRun("e2e", "pending")], "failed"),
        ([], "pending"),
    ],
)
def test_checks_mapping(h: Harness, runs: list[CheckRun], state: str) -> None:
    opened = run(h.adapter.open_change("gen/t-1", "t", BODY, files=FILES))
    assert run(h.adapter.get_checks(opened.number)).state == "passed"  # 새 변경 기본 CI 통과
    h.set_checks(opened.number, runs)
    checks = run(h.adapter.get_checks(opened.number))
    assert checks.state == state
    assert [(r.name, r.state) for r in checks.runs] == [(r.name, r.state) for r in runs]


# ---------------------------------------------------------------- 공용 도우미


def _seed_files(h: Harness, files: dict[str, str]) -> None:
    if h.name == "mock":
        h.adapter.seed_files(files)
    else:
        h.server.seed_files(files)  # type: ignore[attr-defined]


BASE_FILES = {
    "shop-web/a.tsx": "export const a = 1\n",
    "shop-web/b/c.ts": "export const c = 2\n",
    "docs/x.md": "# x\n",
}


def test_list_files_prefix_and_limit(h: Harness) -> None:
    _seed_files(h, BASE_FILES)
    files = run(h.adapter.list_files())
    assert set(BASE_FILES) <= set(files) and files == sorted(files)
    assert "shop-web" not in files  # 폴더는 넣지 않는다
    assert run(h.adapter.list_files("shop-web/")) == ["shop-web/a.tsx", "shop-web/b/c.ts"]
    assert len(run(h.adapter.list_files(limit=2))) == 2


def test_read_file_base_and_errors(h: Harness) -> None:
    _seed_files(
        h,
        {
            **BASE_FILES,
            "shop-web/logo.bin": "PNG\x00\x01",
            "shop-web/big.ts": "x" * (100 * 1024 + 1),
        },
    )
    assert run(h.adapter.read_file("shop-web/b/c.ts")) == "export const c = 2\n"
    cases = {
        "shop-web/none.ts": "not_found",
        "../etc/passwd": "invalid",
        "/shop-web/a.tsx": "invalid",
        "shop-web/logo.bin": "invalid",
        "shop-web/big.ts": "invalid",
    }
    for path, code in cases.items():
        with pytest.raises(VcsError) as err:
            run(h.adapter.read_file(path))
        assert err.value.code == code, path


def test_update_change_adds_commit_on_branch(h: Harness) -> None:
    _seed_files(h, BASE_FILES)
    opened = run(
        h.adapter.open_change("gen/t-d1-1", "R1: 배너", BODY, files={"shop-web/a.tsx": "A\n"})
    )
    assert run(h.adapter.read_file("shop-web/a.tsx", ref="gen/t-d1-1")) == "A\n"
    assert run(h.adapter.read_file("shop-web/a.tsx")) == "export const a = 1\n"
    again = run(
        h.adapter.update_change(
            opened.number, {"shop-web/a.tsx": "AA\n", "shop-web/new.ts": "n\n"}, "리뷰 반영"
        )
    )
    assert (again.number, again.branch) == (opened.number, "gen/t-d1-1")
    assert run(h.adapter.read_file("shop-web/a.tsx", ref="gen/t-d1-1")) == "AA\n"
    assert run(h.adapter.read_file("shop-web/new.ts", ref="gen/t-d1-1")) == "n\n"
    change = run(h.adapter.get_change(opened.number))
    assert set(change.changed_paths) == {"shop-web/a.tsx", "shop-web/new.ts"}
    with pytest.raises(VcsError) as err:
        run(h.adapter.update_change(opened.number, {}, "빈 커밋"))
    assert err.value.code == "invalid"
    run(h.adapter.merge_change(opened.number))
    with pytest.raises(VcsError) as err:
        run(h.adapter.update_change(opened.number, {"shop-web/a.tsx": "B\n"}, "병합 뒤"))
    assert err.value.code == "conflict"


def test_mock_merge_moves_files_to_base() -> None:
    adapter = MockVcsAdapter()
    adapter.seed_files(BASE_FILES)
    opened = run(adapter.open_change("gen/t-1", "t", BODY, files={"shop-web/a.tsx": "A\n"}))
    run(adapter.merge_change(opened.number))
    assert run(adapter.read_file("shop-web/a.tsx")) == "A\n"


def test_github_update_and_reads_use_expected_routes() -> None:
    h = _github_harness()
    _seed_files(h, BASE_FILES)
    server = h.server  # type: ignore[attr-defined]
    opened = run(h.adapter.open_change("gen/t-2", "t", BODY, files={"shop-web/a.tsx": "A\n"}))
    server.seen.clear()
    run(h.adapter.update_change(opened.number, {"shop-web/a.tsx": "B\n"}, "fix"))
    calls = [(r.method, r.url.path.removeprefix(f"/repos/{REPO}")) for r in server.seen]
    assert calls[0] == ("GET", f"/pulls/{opened.number}")
    assert calls[-1] == ("PATCH", "/git/refs/heads/gen/t-2")
    server.seen.clear()
    run(h.adapter.list_files())
    run(h.adapter.read_file("shop-web/a.tsx", ref="gen/t-2"))
    paths = [r.url.path.removeprefix(f"/repos/{REPO}") for r in server.seen]
    assert paths[0] == "/git/ref/heads/dev" and paths[1].startswith("/git/trees/")
    assert paths[2] == "/contents/shop-web/a.tsx"
    assert server.seen[2].url.params["ref"] == "gen/t-2"
    assert all(FAKE_TOKEN not in str(r.url) for r in server.seen)


def test_parse_trace_orders_and_dedupes() -> None:
    meta = parse_trace(3, "SPEC-5:R3 결제", "REQ-9 REQ-9 SPEC-5 R2 PR3 SPEC-6:C1 R2")
    assert meta.requests == ("REQ-9",)
    assert meta.specs == ("SPEC-5", "SPEC-6")
    assert meta.paras == ("SPEC-5:R3", "SPEC-6:C1", "R2")


def test_added_lines_from_patch() -> None:
    patch = "@@ -1,3 +1,4 @@\n a\n-b\n+B\n+C\n c\n@@ -10,2 +11,2 @@\n x\n+y\n\\ No newline"
    assert added_lines(patch) == (2, 3, 12)
    assert added_lines(None) == ()


def test_read_only_mock_refuses_writes() -> None:
    adapter = MockVcsAdapter(read_only=True)
    assert adapter.capabilities.read_only
    with pytest.raises(VcsError) as err:
        run(adapter.open_change("gen/t-1", "t", BODY, files=FILES))
    assert err.value.code == "read_only"
    assert adapter.writes == []


# ---------------------------------------------------------------- GitHub 부품 전용


@pytest.mark.parametrize(
    ("status", "body", "headers", "code"),
    [
        (401, {"message": "Bad credentials"}, {}, "unauthorized"),
        (403, {"message": "Resource not accessible by integration"}, {}, "forbidden"),
        (
            403,
            {"message": "API rate limit exceeded"},
            {"x-ratelimit-remaining": "0"},
            "rate_limited",
        ),
        (429, {"message": "slow down"}, {}, "rate_limited"),
        (404, {"message": "Not Found"}, {}, "not_found"),
        (409, {"message": "Head branch was modified"}, {}, "conflict"),
        (422, {"message": "Validation Failed"}, {}, "invalid"),
        (500, {"message": "boom"}, {}, "unavailable"),
        (502, None, {}, "unavailable"),
    ],
)
def test_github_error_mapping(status, body, headers, code) -> None:
    h = _github_harness()
    res = (
        httpx.Response(status, json=body, headers=headers)
        if body is not None
        else httpx.Response(status, text="<html>bad gateway</html>")
    )
    h.server.fail[("GET", r"/pulls/\d+")] = res  # type: ignore[attr-defined]
    with pytest.raises(VcsError) as err:
        run(h.adapter.get_trace_meta(1))
    assert err.value.code == code
    assert str(status) in err.value.message


def test_github_transport_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect {request.headers['authorization']}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    adapter = GithubVcsAdapter(REPO, FAKE_TOKEN, client=client)
    with pytest.raises(VcsError) as err:
        run(adapter.get_change(1))
    assert err.value.code == "unavailable"
    assert FAKE_TOKEN not in str(err.value) and FAKE_TOKEN not in repr(err.value)


def test_github_token_never_leaks() -> None:
    h = _github_harness()
    # 서버가 토큰을 그대로 되돌려 보내도 메시지에 남기지 않는다
    h.server.fail[("GET", r"/pulls/\d+")] = httpx.Response(  # type: ignore[attr-defined]
        403, json={"message": f"token {FAKE_TOKEN} has no access"}
    )
    with pytest.raises(VcsError) as err:
        run(h.adapter.get_change(1))
    for text in (str(err.value), repr(err.value), err.value.message):
        assert FAKE_TOKEN not in text
    assert "***" in err.value.message
    assert FAKE_TOKEN not in repr(h.adapter) and FAKE_TOKEN not in str(h.adapter)
    # 토큰은 Authorization 헤더로만 간다 (주소·본문에 없다)
    run(h.adapter.open_change("gen/t-1", "t", BODY, files=FILES))
    for req in h.server.seen:  # type: ignore[attr-defined]
        assert req.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
        assert FAKE_TOKEN not in str(req.url) and FAKE_TOKEN.encode() not in req.content


def test_github_wrong_token_is_unauthorized() -> None:
    server = FakeGithub(token="other")
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    adapter = GithubVcsAdapter(REPO, FAKE_TOKEN, client=client)
    with pytest.raises(VcsError) as err:
        run(adapter.open_change("gen/t-1", "t", BODY, files=FILES))
    assert err.value.code == "unauthorized"
    assert server.refs.keys() == {"dev"}  # 아무것도 쓰지 않았다


def test_github_uses_git_data_api_then_pulls() -> None:
    h = _github_harness()
    run(h.adapter.open_change("gen/t-11", "R1", BODY, files=FILES))
    calls = [(r.method, r.url.path.removeprefix(f"/repos/{REPO}")) for r in h.server.seen]  # type: ignore[attr-defined]
    assert calls[0] == ("GET", "/git/ref/heads/dev")
    assert [c[1] for c in calls[2:]] == [
        "/git/blobs",
        "/git/trees",
        "/git/commits",
        "/git/refs",
        "/pulls",
    ]
    pr = h.server.pulls[1]  # type: ignore[attr-defined]
    assert (pr["head"]["ref"], pr["base"]["ref"]) == ("gen/t-11", "dev")


def test_github_config_errors() -> None:
    with pytest.raises(VcsError) as err:
        GithubVcsAdapter("not-a-repo", FAKE_TOKEN)
    assert err.value.code == "config"
    with pytest.raises(VcsError) as err:
        GithubVcsAdapter(REPO, "")
    assert err.value.code == "config"


# ---------------------------------------------------------------- 설정으로 고르기


def test_get_vcs_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANNING_VCS_KIND", raising=False)
    vcs.set_vcs(None)
    assert vcs.configured_kind() == "mock"
    assert isinstance(vcs.get_vcs(), MockVcsAdapter)
    assert vcs.get_vcs() is vcs.get_vcs()  # 프로세스마다 하나
    assert vcs_bridge.active() is None  # 목 설정이면 도메인 동작은 그대로


def test_get_vcs_github_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_KIND", "github")
    monkeypatch.setenv("PLANNING_VCS_REPO", REPO)
    monkeypatch.setenv("PLANNING_VCS_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("PLANNING_VCS_BASE_URL", "https://ghe.example.com/api/v3/")
    monkeypatch.setenv("PLANNING_VCS_BASE_BRANCH", "main")
    vcs.set_vcs(None)
    adapter = vcs.get_vcs()
    assert isinstance(adapter, GithubVcsAdapter)
    assert (adapter.repo, adapter.base_url, adapter.base_branch) == (
        REPO,
        "https://ghe.example.com/api/v3",
        "main",
    )
    assert FAKE_TOKEN not in repr(adapter)


def test_get_vcs_bad_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_KIND", "svn")
    vcs.set_vcs(None)
    with pytest.raises(VcsError) as err:
        vcs.get_vcs()
    assert err.value.code == "config"
    monkeypatch.setenv("PLANNING_VCS_KIND", "github")
    monkeypatch.delenv("PLANNING_VCS_TOKEN", raising=False)
    monkeypatch.setenv("PLANNING_VCS_REPO", REPO)
    vcs.set_vcs(None)
    with pytest.raises(VcsError) as err:
        vcs.get_vcs()
    assert err.value.code == "config"


# ---------------------------------------------------------------- 도메인 연결


def _req12_to_prs(service: PlanningService) -> None:
    run(service.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    run(service.confirm_spec("SPEC-7"))
    run(service.generate_prs("SPEC-7"))


def _prs(service: PlanningService):
    return {p.id: p for p in service.state.prs if p.task in {"T-11", "T-12"}}


def test_domain_opens_and_merges_through_adapter(service: PlanningService) -> None:
    adapter = MockVcsAdapter(first_number=500)
    vcs.set_vcs(adapter)
    _req12_to_prs(service)
    prs = _prs(service)
    pr31, pr32 = prs["PR #31"], prs["PR #32"]
    assert (pr31.vcs_number, pr31.url) == (500, "mock://change/500")
    assert pr32.vcs_number == 501
    # PR #31은 자동 머지 → 부품에서도 병합. PR #32는 사람 확인 대기 → 아직 안 함
    assert adapter.writes == [("open_change", 500), ("open_change", 501), ("merge_change", 500)]
    meta = run(adapter.get_trace_meta(500))
    assert (meta.requests, meta.specs, meta.paras[0]) == (("REQ-12",), ("SPEC-7",), "SPEC-7:R1")
    change = run(adapter.get_change(500))
    assert change.branch == "gen/t-11" and change.merged
    run(service.merge_pr("PR #32"))
    assert adapter.writes[-1] == ("merge_change", 501)
    assert _prs(service)["PR #32"].merged
    points = [a.point for a in service.state.activity]
    assert points.count("vcs.opened") == 2 and points.count("vcs.merged") == 2


def test_domain_merge_failure_keeps_pr_unmerged(service: PlanningService) -> None:
    class BrokenMerge(MockVcsAdapter):
        async def merge_change(self, number: int, method: str = "squash"):
            raise VcsError("conflict", "merge conflict")

    adapter = BrokenMerge()
    vcs.set_vcs(adapter)
    _req12_to_prs(service)
    pr31 = _prs(service)["PR #31"]
    assert pr31.vcs_number == 1 and not pr31.merged and pr31.merged_at is None
    task = next(t for t in service.state.tasks if t.id == "T-11")
    assert task.state == "머지 대기"
    failed = [a for a in service.state.activity if a.point == "vcs.merge_failed"]
    assert failed and "conflict" in failed[0].text
    # 나중에 부품이 고쳐지면 사람 머지 버튼으로 다시 시도한다
    vcs.set_vcs(MockVcsAdapter())
    run(service.merge_pr("PR #31"))
    assert not _prs(service)["PR #31"].merged  # 새 부품에는 그 변경이 없다 → 여전히 안 함


def test_domain_open_failure_records_line(service: PlanningService) -> None:
    class BrokenOpen(MockVcsAdapter):
        async def open_change(self, *a, **kw):
            raise VcsError("forbidden", "no access")

    vcs.set_vcs(BrokenOpen())
    _req12_to_prs(service)
    pr31 = _prs(service)["PR #31"]
    assert pr31.vcs_number is None and pr31.url is None and not pr31.merged
    points = [a.point for a in service.state.activity]
    assert "vcs.open_failed" in points and "vcs.merge_failed" in points


def test_domain_bad_config_fails_closed(service: PlanningService, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_KIND", "github")
    monkeypatch.delenv("PLANNING_VCS_REPO", raising=False)
    monkeypatch.delenv("PLANNING_VCS_TOKEN", raising=False)
    vcs.set_vcs(None)
    _req12_to_prs(service)
    assert not _prs(service)["PR #31"].merged
    assert "vcs.open_failed" in [a.point for a in service.state.activity]


def test_domain_mock_kind_unchanged(monkeypatch) -> None:
    """목 설정이면 부품을 부르지 않고 상태가 지금과 똑같다."""
    monkeypatch.delenv("PLANNING_VCS_KIND", raising=False)
    a, b = PlanningService(), PlanningService()
    for svc in (a, b):
        set_service(svc)
        _req12_to_prs(svc)
    assert a.snapshot() == b.snapshot()
    assert all(p.vcs_number is None and p.url is None for p in a.state.prs)
    assert not any(x.point.startswith("vcs.") for x in a.state.activity)


def test_bridge_merge_is_safe_to_retry() -> None:
    """앞선 시도에서 병합만 되고 상태가 버려졌어도, 다시 병합하면 성공으로 본다."""
    from planning_agent.domain.models import PR, PrGate

    adapter = MockVcsAdapter()
    vcs.set_vcs(adapter)
    pr = PR(
        id="PR #40",
        number=40,
        task="T-40",
        title="R1: 표시",
        branch="gen/t-40",
        reviews=["통과"],
        gate=PrGate(result="통과", lines=1, files=1, paths="shop-web"),
        merged=False,
        changed_paths=["shop-web/a.tsx"],
    )
    assert run(vcs_bridge.open_pr(pr, "REQ-1", "SPEC-1", "R1")).ok
    assert run(vcs_bridge.merge_pr(pr)).ok
    again = run(vcs_bridge.merge_pr(pr))
    assert again.ok and adapter.writes.count(("merge_change", pr.vcs_number)) == 1


def test_domain_simple_request_opens_and_merges(service: PlanningService) -> None:
    """간단 요청 자동 처리 경로도 관문 뒤에 부품으로 올리고 병합한다."""
    adapter = MockVcsAdapter(first_number=700)
    vcs.set_vcs(adapter)
    run(service.inbound("support_simple"))
    auto = [p for p in service.state.prs if p.auto]
    assert auto and auto[-1].vcs_number == 700 and auto[-1].merged
    assert adapter.writes == [("open_change", 700), ("merge_change", 700)]


def test_domain_simple_request_merge_failure_waits(service: PlanningService) -> None:
    class BrokenMerge(MockVcsAdapter):
        async def merge_change(self, number: int, method: str = "squash"):
            raise VcsError("conflict", "merge conflict")

    vcs.set_vcs(BrokenMerge())
    run(service.inbound("support_simple"))
    pr = [p for p in service.state.prs if p.auto][-1]
    task = next(t for t in service.state.tasks if t.id == pr.task)
    assert not pr.merged and task.state == "머지 대기"


def test_github_checks_fall_back_to_statuses_without_checks_permission() -> None:
    """개인용 토큰은 Checks 권한이 없다. check-runs가 403이어도 커밋 상태로 판단한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7"):
            return httpx.Response(200, json={"head": {"sha": "abc"}})
        if path.endswith("/check-runs"):
            return httpx.Response(
                403, json={"message": "Resource not accessible by personal access token"}
            )
        if path.endswith("/status"):
            return httpx.Response(200, json={"statuses": [{"context": "ci", "state": "success"}]})
        return httpx.Response(404, json={})

    from planning_agent.vcs.github import GithubVcsAdapter

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GithubVcsAdapter("acme/shop", "t" * 12, client=client)
    checks = run(adapter.get_checks(7))
    assert checks.state == "passed" and [r.name for r in checks.runs] == ["ci"]


def test_open_conflict_adopts_existing_change() -> None:
    """앞선 시도에서 브랜치·PR이 이미 올라갔으면 새로 만들지 않고 이어받는다."""
    from planning_agent import vcs_bridge
    from planning_agent.domain.models import PR, PrGate

    adapter = MockVcsAdapter(first_number=900)
    vcs.set_vcs(adapter)
    try:
        files = {"docs/generated/spec-1/t-12.md": "x"}  # 이 PR이 올리는 파일과 같다
        run(adapter.open_change(branch="gen/t-12", title="t", body="b", files=files))
        run(adapter.merge_change(900))
        pr = PR(
            id="PR #32",
            number=32,
            task="T-12",
            title="t",
            branch="gen/t-12",
            reviews=["통과"],
            gate=PrGate(result="통과", lines=1, files=1, paths="docs/"),
            merged=False,
        )
        note = run(vcs_bridge.open_pr(pr, "REQ-1", "SPEC-1", "R1"))
        assert note is not None and note.ok and note.point == "vcs.adopted"
        assert pr.vcs_number == 900 and "이미 병합됨" in note.text
        merged = run(vcs_bridge.merge_pr(pr))
        assert merged is not None and merged.ok
    finally:
        vcs.set_vcs(None)


def test_open_conflict_with_other_tasks_branch_opens_new_branch() -> None:
    """서버를 다시 켜 작업 번호가 겹쳐 브랜치 이름이 같아도, 다른 작업의 (머지된) PR은
    이어받지 않고 새 브랜치로 올린다."""
    from planning_agent import vcs_bridge
    from planning_agent.domain.models import PR, PrGate

    adapter = MockVcsAdapter(first_number=4)
    vcs.set_vcs(adapter)
    try:
        old = {"src/ShopShell.tsx": "팝업"}
        run(adapter.open_change(branch="gen/t-d1-1", title="R1: 팝업", body="b", files=old))
        run(adapter.merge_change(4))
        pr = PR(id="PR #31", number=31, task="T-D1", title="R1: 보라색", branch="gen/t-d1-1",
                reviews=[], gate=PrGate(result="통과", lines=1, files=1, paths="src/"),
                merged=False)  # fmt: skip
        files = {"src/styles.css": "purple"}
        note = run(vcs_bridge.open_pr(pr, None, None, None, files=files, body="b"))
        assert note is not None and note.ok and note.point == "vcs.opened"
        assert pr.vcs_number == 5 and pr.branch.startswith("gen/t-d1-1-")
        assert "다른 작업이 써서 새 브랜치" in note.text
        assert not run(adapter.get_change(5)).merged
    finally:
        vcs.set_vcs(None)
