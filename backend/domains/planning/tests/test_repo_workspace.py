"""레포 작업 공간 (개발 화면에서 PR·브랜치 직접 다루기).

목 부품·GitHub 부품(가짜 서버 `httpx.MockTransport`)의 새 인터페이스와 `/api/planning/repo/*` REST,
사람 토큰(401·403)을 본다. 실제 GitHub는 부르지 않는다. 토큰은 가짜 값이다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from planning_agent import vcs
from planning_agent.main import app
from planning_agent.runtime import get_service
from planning_agent.vcs import MockVcsAdapter, VcsError
from planning_agent.vcs.base import DIFF_FILE_CHARS, FileDiff, cap_diff
from planning_agent.vcs.github import GithubVcsAdapter

FAKE_TOKEN = "fake-token-0123456789"
REPO = "acme/shop"
BASE = "/api/planning/repo"
T0 = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_override():
    vcs._screen = None
    vcs.set_vcs(None)
    yield
    vcs._screen = None
    vcs.set_vcs(None)


def _mock() -> MockVcsAdapter:
    m = MockVcsAdapter(clock=lambda: T0)
    m.seed_files({"shop-web/a.ts": "one\ntwo\nthree\n", "docs/b.md": "b\n"})
    return m


# ---------------------------------------------------------------- 목 부품


def test_mock_diff_has_real_patch_lines() -> None:
    m = _mock()
    opened = run(
        m.open_change(
            "gen/t-1",
            "R1: 문구 바꾸기",
            "요청: REQ-1",
            files={"shop-web/a.ts": "one\nTWO\nthree\n", "docs/new.md": "new\n"},
        )
    )
    diff = run(m.get_diff(opened.number))
    by = {f.path: f for f in diff.files}
    a = by["shop-web/a.ts"]
    assert (a.status, a.additions, a.deletions) == ("modified", 1, 1)
    assert "-two" in (a.patch or "") and "+TWO" in (a.patch or "")
    assert a.patch is not None and a.patch.startswith("@@")
    assert by["docs/new.md"].status == "added"
    assert diff.truncated is False and diff.total_files == 2


def test_mock_list_filters_and_search() -> None:
    m = _mock()
    human = m.seed(title="사람이 올린 수정", branch="fix/login", author="kim", files={"a": (1, 0)})
    merged = m.seed(title="머지된 것", branch="feat/x", merged_at=T0)
    closed = m.seed(title="닫힌 것", branch="feat/y", closed=True)
    assert [c.number for c in run(m.list_changes("open"))] == [human]
    assert [c.number for c in run(m.list_changes("merged"))] == [merged]
    assert [c.number for c in run(m.list_changes("closed"))] == [closed]
    assert len(run(m.list_changes("all"))) == 3
    assert [c.number for c in run(m.list_changes("all", "login"))] == [human]
    assert [c.number for c in run(m.list_changes("all", f"#{merged}"))] == [merged]
    assert [c.number for c in run(m.list_changes("all", "KIM"))] == [human]
    assert run(m.list_changes("open"))[0].author == "kim"
    with pytest.raises(VcsError) as exc:
        run(m.list_changes("weird"))
    assert exc.value.code == "invalid"


def test_mock_comment_close_reopen() -> None:
    m = _mock()
    n = m.seed(title="t", branch="fix/a")
    c = run(m.add_comment(n, "  확인했어요  "))
    assert c.body == "확인했어요"
    assert [x.body for x in run(m.list_comments(n))] == ["확인했어요"]
    assert run(m.describe_change(n)).comments == 1
    run(m.close_change(n))
    assert run(m.describe_change(n)).state == "closed"
    assert run(m.reopen_change(n)).state == "open"
    run(m.merge_change(n, "merge"))
    with pytest.raises(VcsError) as exc:
        run(m.reopen_change(n))
    assert exc.value.code == "conflict"


def test_mock_revert_builds_inverse_change() -> None:
    m = _mock()
    opened = run(
        m.open_change(
            "gen/t-2",
            "바꾸기",
            "",
            files={"shop-web/a.ts": "changed\n", "docs/new.md": "new\n"},
        )
    )
    with pytest.raises(VcsError) as exc:
        run(m.revert_change(opened.number))
    assert exc.value.code == "conflict"  # 병합 전에는 되돌리지 않는다
    run(m.merge_change(opened.number, "merge"))
    assert run(m.read_file("docs/new.md")) == "new\n"
    rev = run(m.revert_change(opened.number))
    assert rev.branch.startswith(f"revert-{opened.number}-")
    head = run(m.describe_change(rev.number))
    assert head.title == 'Revert "바꾸기"' and head.state == "open"
    by = {f.path: f for f in run(m.get_diff(rev.number)).files}
    assert by["docs/new.md"].status == "removed"
    assert "+one" in (by["shop-web/a.ts"].patch or "")
    run(m.merge_change(rev.number, "merge"))
    assert run(m.read_file("shop-web/a.ts")) == "one\ntwo\nthree\n"
    with pytest.raises(VcsError):
        run(m.read_file("docs/new.md"))


def test_mock_branches_and_delete() -> None:
    m = _mock()
    m.seed(title="t", branch="fix/a")
    names = {b.name: b for b in run(m.list_branches())}
    assert names["dev"].default and names["dev"].protected
    assert "fix/a" in names
    with pytest.raises(VcsError) as exc:
        run(m.delete_branch("dev"))
    assert exc.value.code == "invalid"
    run(m.delete_branch("fix/a"))
    assert "fix/a" not in {b.name for b in run(m.list_branches())}
    with pytest.raises(VcsError) as exc:
        run(m.delete_branch("fix/a"))
    assert exc.value.code == "not_found"


def test_cap_diff_marks_truncation() -> None:
    big = "".join(f"+line {i}\n" for i in range(5000))
    diff = cap_diff(1, [FileDiff(path="a", patch=big, additions=5000)])
    f = diff.files[0]
    assert f.truncated and f.patch is not None and len(f.patch) <= DIFF_FILE_CHARS
    assert f.patch.endswith("\n")  # 줄 중간에서 자르지 않는다
    many = cap_diff(1, [FileDiff(path=f"f{i}") for i in range(301)])
    assert many.truncated and len(many.files) == 300 and many.total_files == 301


# ---------------------------------------------------------------- GitHub 부품 (가짜 서버)


def _pr(number: int, *, state="open", merged=False, branch=None, title=None) -> dict:
    return {
        "number": number,
        "node_id": f"PR_node{number}",
        "title": title or f"PR {number}",
        "body": "본문",
        "state": state,
        "merged": merged,
        "merged_at": "2026-10-28T01:00:00Z" if merged else None,
        "closed_at": "2026-10-28T01:00:00Z" if state == "closed" else None,
        "created_at": "2026-10-27T01:00:00Z",
        "updated_at": "2026-10-28T01:00:00Z",
        "draft": False,
        "user": {"login": "kim"},
        "head": {"ref": branch or f"feat/{number}", "sha": f"sha{number}"},
        "base": {"ref": "dev"},
        "html_url": f"https://github.example/{REPO}/pull/{number}",
        "changed_files": 1,
    }


class Fake:
    def __init__(self, base: str = "https://api.github.com") -> None:
        self.base = base
        self.pulls = {1: _pr(1), 2: _pr(2, state="closed", merged=True), 3: _pr(3, state="closed")}
        self.seen: list[httpx.Request] = []
        self.graphql: list[dict] = []
        self.graphql_reply: dict = {
            "data": {
                "revertPullRequest": {
                    "revertPullRequest": {
                        "number": 9,
                        "url": "https://github.example/pull/9",
                        "headRefName": "revert-2-feat/2",
                    }
                }
            }
        }
        self.deleted: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
        path = req.url.path
        api = httpx.URL(self.base).path.rstrip("/")
        if path.endswith("/graphql"):
            self.graphql.append(json.loads(req.content))
            return httpx.Response(200, json=self.graphql_reply)
        prefix = f"{api}/repos/{REPO}"
        assert path.startswith(prefix), path
        rest = path[len(prefix) :]
        m = req.method
        if m == "GET" and rest == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if m == "GET" and rest == "/pulls":
            state = req.url.params.get("state")
            rows = [p for p in self.pulls.values() if state in ("all", p["state"])]
            return httpx.Response(200, json=sorted(rows, key=lambda p: -p["number"]))
        if m == "GET" and rest == "/issues":
            return httpx.Response(
                200, json=[{"number": 1, "comments": 4, "pull_request": {}}, {"number": 7}]
            )
        if rest.startswith("/pulls/"):
            parts = rest.split("/")
            n = int(parts[2])
            if m == "GET" and len(parts) == 3:
                return httpx.Response(200, json=self.pulls[n])
            if m == "PATCH" and len(parts) == 3:
                self.pulls[n] = {**self.pulls[n], **json.loads(req.content)}
                return httpx.Response(200, json=self.pulls[n])
            if parts[3] == "files":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "filename": "shop-web/a.ts",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "patch": "@@ -1,2 +1,2 @@\n-old\n+new\n ctx",
                        }
                    ],
                )
            if parts[3] == "commits":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "sha": "abc",
                            "commit": {
                                "message": "fix: 고침\n\n본문",
                                "author": {"name": "Kim", "date": "2026-10-27T02:00:00Z"},
                            },
                            "author": {"login": "kim"},
                        }
                    ],
                )
        if rest.startswith("/issues/") and rest.endswith("/comments"):
            if m == "POST":
                body = json.loads(req.content)["body"]
                return httpx.Response(
                    201,
                    json={
                        "id": 55,
                        "user": {"login": "me"},
                        "body": body,
                        "created_at": "2026-10-29T01:00:00Z",
                    },
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "user": {"login": "lee"},
                        "body": "봤어요",
                        "created_at": "2026-10-28T01:00:00Z",
                    }
                ],
            )
        if rest.startswith("/commits/") and rest.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]
                },
            )
        if rest.startswith("/commits/") and rest.endswith("/status"):
            return httpx.Response(200, json={"statuses": []})
        if m == "GET" and rest.startswith("/commits/"):
            return httpx.Response(
                200, json={"commit": {"committer": {"date": "2026-10-20T00:00:00Z"}}}
            )
        if m == "GET" and rest == "/branches":
            return httpx.Response(
                200,
                json=[
                    {"name": "dev", "commit": {"sha": "d1"}, "protected": True},
                    {"name": "main", "commit": {"sha": "m1"}, "protected": True},
                    {"name": "feat/1", "commit": {"sha": "f1"}, "protected": False},
                ],
            )
        if m == "GET" and rest.startswith("/compare/"):
            return httpx.Response(
                200,
                json={
                    "ahead_by": 2,
                    "behind_by": 3,
                    "commits": [{"commit": {"committer": {"date": "2026-10-25T00:00:00Z"}}}],
                },
            )
        if m == "DELETE" and rest.startswith("/git/refs/heads/"):
            self.deleted.append(rest[len("/git/refs/heads/") :])
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "Not Found"})


def _gh(fake: Fake) -> GithubVcsAdapter:
    return GithubVcsAdapter(
        REPO,
        FAKE_TOKEN,
        base_url=fake.base,
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )


def test_github_list_changes_states_search_ci_comments() -> None:
    fake = Fake()
    gh = _gh(fake)
    rows = run(gh.list_changes("open"))
    assert [r.number for r in rows] == [1]
    assert rows[0].author == "kim" and rows[0].checks == "passed" and rows[0].comments == 4
    assert [r.number for r in run(gh.list_changes("merged"))] == [2]
    assert [r.number for r in run(gh.list_changes("closed"))] == [3]
    assert [r.number for r in run(gh.list_changes("all", "#3"))] == [3]
    assert [r.number for r in run(gh.list_changes("all", "feat/2"))] == [2]


def test_github_detail_diff_commits_comments() -> None:
    gh = _gh(Fake())
    head = run(gh.describe_change(1))
    assert (head.state, head.branch, head.base, head.body) == ("open", "feat/1", "dev", "본문")
    diff = run(gh.get_diff(1))
    assert diff.files[0].patch and "+new" in diff.files[0].patch
    commits = run(gh.list_commits(1))
    assert commits[0].author == "kim" and commits[0].message.startswith("fix: 고침")
    assert run(gh.list_comments(1))[0].body == "봤어요"
    assert run(gh.add_comment(1, "좋아요")).body == "좋아요"


def test_github_reopen_and_revert_uses_graphql() -> None:
    fake = Fake()
    gh = _gh(fake)
    assert run(gh.reopen_change(3)).state == "open"
    with pytest.raises(VcsError) as exc:
        run(gh.reopen_change(2))
    assert exc.value.code == "conflict"
    with pytest.raises(VcsError) as exc:
        run(gh.revert_change(1))  # 병합 전
    assert exc.value.code == "conflict"
    got = run(gh.revert_change(2))
    assert (got.number, got.branch) == (9, "revert-2-feat/2")
    sent = fake.graphql[-1]
    assert "revertPullRequest" in sent["query"]
    assert sent["variables"] == {"id": "PR_node2", "title": 'Revert "PR 2"'}
    assert str(fake.seen[-1].url) == "https://api.github.com/graphql"


def test_github_enterprise_graphql_url_and_errors_hide_token() -> None:
    fake = Fake(base="https://ghe.example/api/v3")
    fake.graphql_reply = {"errors": [{"type": "FORBIDDEN", "message": f"no {FAKE_TOKEN}"}]}
    gh = _gh(fake)
    with pytest.raises(VcsError) as exc:
        run(gh.revert_change(2))
    assert exc.value.code == "forbidden"
    assert FAKE_TOKEN not in exc.value.message
    assert str(fake.seen[-1].url) == "https://ghe.example/api/graphql"


def test_github_branches_compare_and_delete_rules() -> None:
    fake = Fake()
    gh = _gh(fake)
    rows = {b.name: b for b in run(gh.list_branches())}
    assert rows["main"].default and rows["dev"].ahead == 0
    assert (rows["feat/1"].ahead, rows["feat/1"].behind) == (2, 3)
    assert rows["feat/1"].last_commit_at == datetime(2026, 10, 25, tzinfo=UTC)
    for name in ("dev", "main"):
        with pytest.raises(VcsError) as exc:
            run(gh.delete_branch(name))
        assert exc.value.code == "invalid"
    run(gh.delete_branch("feat/1"))
    assert fake.deleted == ["feat/1"]


# ---------------------------------------------------------------- REST


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def repo() -> MockVcsAdapter:
    m = _mock()
    vcs.set_vcs(m)
    return m


def test_no_connection_is_409(client) -> None:
    res = client.get(f"{BASE}/prs")
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_VCS"


def test_list_includes_human_prs_and_marks_ai_ones(client, repo) -> None:
    human = repo.seed(title="사람이 직접 올림", branch="fix/human", author="kim")
    ai = repo.seed(title="AI 작업", branch="gen/t-9")
    svc = get_service()
    svc.state.prs[0].vcs_number = ai  # PR #28이 이 변경으로 올라간 것으로 둔다
    res = client.get(f"{BASE}/prs", params={"state": "all"})
    assert res.status_code == 200
    data = res.json()["data"]
    by = {x["number"]: x for x in data["items"]}
    assert by[human]["internal"] is None and by[human]["author"] == "kim"
    assert by[ai]["internal"]["id"] == "PR #28"
    assert "body" not in by[human]
    assert data["base_branch"] == "dev"
    assert client.get(f"{BASE}/prs", params={"state": "bad"}).status_code == 400
    assert client.get(f"{BASE}/prs", params={"connection": "nope"}).status_code == 404


def test_detail_has_diff_commits_comments_checks(client, repo) -> None:
    opened = run(repo.open_change("gen/t-3", "R1: x", "", files={"shop-web/a.ts": "one\n"}))
    run(repo.add_comment(opened.number, "코멘트"))
    res = client.get(f"{BASE}/prs/{opened.number}")
    assert res.status_code == 200
    d = res.json()["data"]
    assert d["pr"]["state"] == "open" and d["pr"]["branch"] == "gen/t-3"
    assert d["diff"]["files"][0]["path"] == "shop-web/a.ts"
    assert d["commits"][0]["message"] == "R1: x"
    assert d["comments"][0]["body"] == "코멘트"
    assert d["checks"]["state"] == "passed"
    assert client.get(f"{BASE}/prs/999").status_code == 404


def test_merge_plain_pr_goes_through_merge_pr_gate(client, repo) -> None:
    n = repo.seed(title="사람 PR", branch="fix/a", files={"shop-web/a.ts": (3, 1)})
    assert client.post(f"{BASE}/prs/{n}/merge", json={"method": "fast"}).status_code == 400
    res = client.post(f"{BASE}/prs/{n}/merge", json={"method": "squash"})
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["via"] == "repo" and body["pr"]["state"] == "merged"
    assert ("merge_change", n) in repo.writes
    svc = get_service()
    assert any(g.action == "merge_pr" and f"#{n}" in g.idempotency_key for g in svc.gate_calls)
    assert any(a.point == "merge_pr" and a.from_ == "human" for a in svc.state.activity)
    again = client.post(f"{BASE}/prs/{n}/merge", json={})
    assert again.status_code == 409


def test_merge_ai_pr_uses_internal_flow(client, repo) -> None:
    n = repo.seed(title="AI 작업", branch="gen/t-40")
    svc = get_service()
    base = svc.state.prs[0]
    svc.state.prs.append(
        base.model_copy(
            update={
                "id": "PR #40",
                "number": 40,
                "merged": False,
                "merged_at": None,
                "vcs_number": n,
            }
        )
    )
    res = client.post(f"{BASE}/prs/{n}/merge", json={"method": "squash"})
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert body["via"] == "internal" and body["internal"]["merged"] is True
    assert next(p for p in svc.state.prs if p.id == "PR #40").merged
    assert any(
        g.action == "merge_pr" and g.idempotency_key.endswith("PR #40") for g in svc.gate_calls
    )


def test_close_reopen_comment(client, repo) -> None:
    n = repo.seed(title="사람 PR", branch="fix/b")
    assert client.post(f"{BASE}/prs/{n}/close").json()["data"]["pr"]["state"] == "closed"
    assert client.post(f"{BASE}/prs/{n}/reopen").json()["data"]["pr"]["state"] == "open"
    assert client.post(f"{BASE}/prs/{n}/comment", json={"body": " "}).status_code == 400
    res = client.post(f"{BASE}/prs/{n}/comment", json={"body": "봤어요"})
    assert res.status_code == 200 and res.json()["data"]["comment"]["body"] == "봤어요"
    actions = [g.action for g in get_service().gate_calls]
    assert {"repo_close_pr", "repo_reopen_pr", "repo_comment"} <= set(actions)


def test_revert_opens_new_pr(client, repo) -> None:
    opened = run(repo.open_change("gen/t-5", "바꾸기", "", files={"shop-web/a.ts": "x\n"}))
    assert client.post(f"{BASE}/prs/{opened.number}/revert").status_code == 409  # 병합 전
    run(repo.merge_change(opened.number, "merge"))
    res = client.post(f"{BASE}/prs/{opened.number}/revert", json={})
    assert res.status_code == 200, res.text
    new = res.json()["data"]["revert"]["number"]
    head = client.get(f"{BASE}/prs/{new}").json()["data"]["pr"]
    assert head["state"] == "open" and head["title"] == 'Revert "바꾸기"'
    gate = [g for g in get_service().gate_calls if g.action == "repo_revert_pr"]
    assert len(gate) == 1


def test_branches_and_delete_rules(client, repo) -> None:
    open_n = repo.seed(title="열림", branch="fix/open")
    merged = repo.seed(title="머지됨", branch="fix/done", merged_at=T0)
    res = client.get(f"{BASE}/branches")
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert items[0]["name"] == "dev" and items[0]["base"] is True
    by = {b["name"]: b for b in items}
    assert by["fix/open"]["pr"] == {"number": open_n, "state": "open", "title": "열림"}
    assert by["fix/done"]["pr"]["state"] == "merged" and by["fix/done"]["pr"]["number"] == merged
    delete = f"{BASE}/branches/delete"
    assert client.post(delete, json={"name": "dev"}).json()["error"]["code"] == "PROTECTED_BRANCH"
    assert client.post(delete, json={"name": "fix/open"}).json()["error"]["code"] == "OPEN_PR"
    assert client.post(delete, json={"name": "nope"}).status_code == 404
    ok = client.post(delete, json={"name": "fix/done"})
    assert ok.status_code == 200 and ok.json()["data"]["deleted"] == "fix/done"
    names = {b["name"] for b in client.get(f"{BASE}/branches").json()["data"]["items"]}
    assert "fix/done" not in names


def test_read_only_connection_refuses_writes(client) -> None:
    m = MockVcsAdapter(read_only=True)
    n = m.seed(title="t", branch="fix/r")
    vcs.set_vcs(m)
    res = client.post(f"{BASE}/prs/{n}/comment", json={"body": "x"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "READ_ONLY"


WRITES: list[tuple[str, dict[str, Any]]] = [
    ("/prs/1/merge", {}),
    ("/prs/1/close", {}),
    ("/prs/1/reopen", {}),
    ("/prs/1/comment", {"body": "x"}),
    ("/prs/1/revert", {}),
    ("/branches/delete", {"name": "fix/x"}),
]


@pytest.mark.parametrize(("path", "body"), WRITES)
@pytest.mark.parametrize(
    ("headers", "status"),
    [({"Authorization": ""}, 401), ({"Authorization": "Bearer nobody"}, 401)],
)
def test_repo_writes_need_token(client, repo, path, body, headers, status) -> None:
    res = client.post(f"{BASE}{path}", json=body, headers=headers)
    assert res.status_code == status
    assert res.json()["error"]["code"] == "UNAUTHORIZED"
    assert repo.writes == []


@pytest.mark.parametrize(("path", "body"), WRITES)
def test_repo_writes_refuse_agent_token(client, repo, path, body) -> None:
    res = client.post(f"{BASE}{path}", json=body, headers={"Authorization": "Bearer mock:infra"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"
    assert repo.writes == []


def test_github_errors_in_api_hide_token(client) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"Bad credentials {FAKE_TOKEN}"})

    gh = GithubVcsAdapter(
        REPO, FAKE_TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    vcs.set_vcs(gh)
    res = client.get(f"{BASE}/prs")
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "VCS_UNAUTHORIZED"
    assert FAKE_TOKEN not in res.text
