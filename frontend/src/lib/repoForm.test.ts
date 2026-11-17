// node --test로 돈다. 레포 연결 폼(ToolConnections.tsx)의 저장 본문과 입력 확인 (lib/repoForm.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { EMPTY_REPO_FORM, repoAppLabel, repoBody, repoFormError, type RepoForm } from "./repoForm.ts";

const form = (extra: Partial<RepoForm>): RepoForm => ({ ...EMPTY_REPO_FORM, addr: "acme/shop", repo: "acme/shop", ...extra });

test("GitHub App이면 고른 앱 id(app_ref)만 보내고 앱 ID·설치 ID·개인 키 칸은 없다", () => {
  const body = repoBody(form({ auth: "app", app_ref: "github_app-1", token: "github_pat_x" }));
  assert.equal(body.app_ref, "github_app-1");
  assert.equal(body.auth, "app");
  for (const k of ["app_id", "installation_id", "private_key", "token", "addr"]) assert.equal(k in body, false, k);
});

test("토큰이면 넣은 토큰만 보내고, 비우면 보내지 않는다", () => {
  assert.equal(repoBody(form({ token: "github_pat_x" })).token, "github_pat_x");
  const empty = repoBody(form({}));
  assert.equal("token" in empty, false);
  assert.equal("app_ref" in empty, false);
  assert.equal(empty.detect_branch, true);
});

test("GitHub App을 고르지 않았거나 없는 앱이면 저장을 막는다", () => {
  assert.match(repoFormError(form({ auth: "app" }), undefined, []) ?? "", /GitHub 앱 칸/);
  assert.match(repoFormError(form({ auth: "app" }), undefined, ["github_app-1"]) ?? "", /골라/);
  assert.match(repoFormError(form({ auth: "app", app_ref: "github_app-9" }), undefined, ["github_app-1"]) ?? "", /골라/);
  assert.equal(repoFormError(form({ auth: "app", app_ref: "github_app-1" }), undefined, ["github_app-1"]), null);
  assert.match(repoFormError(form({}), undefined, []) ?? "", /접근 토큰/);
  assert.equal(repoFormError(form({}), { token_set: true }, []), null);
});

test("목록에는 가리키는 앱 이름을 보여 준다", () => {
  assert.equal(repoAppLabel({ app_ref: "github_app-1", app_name: "회사 앱" }), "회사 앱");
  assert.match(repoAppLabel({ app_ref: "github_app-1", app_name: "" }), /찾을 수 없음/);
  assert.equal(repoAppLabel({ app_id: "1", installation_id: "2", key_set: true }), "앱 1 · 설치 2");
});
