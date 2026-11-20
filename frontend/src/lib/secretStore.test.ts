// node --test로 돈다. 키 관리 화면의 순수 계산: 값이 어디에도 안 보이는지, 입력 검사, 문구.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canTryApply, hintKo, historyLineKo, nameProblem, openRequests, parseTenants, readSecretState, secretHttpErrorKo, tenantListProblem,
  valueProblem, writeResultKo,
} from "./secretStore.ts";
import { secretEditorProblem, updateBody } from "./memberAdmin.ts";

const DUMMY = "DUMMY-NOT-A-REAL-KEY-0001";

const SERVER = {
  viewer: { can_edit: true, enabled: true, tenants: ["acme"] },
  tenants: [{ tenant: "acme", secrets: [{ tenant: "acme", name: "MARKETING_VIDEO_API_KEY", configured: true, hint: "0001", version: 2, updated_by: "민지", updated_at: "2026-11-20T01:00:00+00:00", value: DUMMY, ciphertext: "x" }] }],
  requests: [
    { id: "sr-1", op: "replace", tenant: "acme", name: "MARKETING_VIDEO_API_KEY", hint: "0002", fingerprint: "ab", reason: "교체", requested_by: "민지", requested_at: "t", review_id: "rev-1" },
    { id: "sr-2", op: "add", tenant: "acme", name: "X_KEY_NAME", hint: "", fingerprint: "", reason: "", requested_by: "민지", requested_at: "t", review_id: null },
  ],
  history: [{ at: "t", by: "민지", action: "request_replace", tenant: "acme", name: "MARKETING_VIDEO_API_KEY", review_id: null }],
  suggested_names: ["MARKETING_VIDEO_API_KEY", 5],
};

test("서버 응답에서 허용 칸만 읽고 값·암호문 칸은 버린다", () => {
  const s = readSecretState(SERVER);
  assert.ok(s);
  assert.equal(JSON.stringify(s).includes(DUMMY), false);
  assert.equal(JSON.stringify(s).includes("ciphertext"), false);
  assert.deepEqual(s.myTenants, ["acme"]);
  assert.deepEqual(s.suggested, ["MARKETING_VIDEO_API_KEY"]);
  assert.equal(readSecretState({ tenants: [] }), null);
});

test("화면 문구는 설정됨과 끝 4자리 이하만", () => {
  const s = readSecretState(SERVER)!;
  const row = s.tenants[0].secrets[0];
  assert.equal(hintKo(row), "설정됨 (…0001)");
  assert.equal(hintKo({ configured: true, hint: "abcdefg" }), "설정됨 (…defg)");
  assert.equal(hintKo(undefined), "미설정");
});

test("교체·삭제 요청만 승인 대기이고, 보류 번호가 있고 담당 고객사일 때만 적용을 눌러 본다", () => {
  const s = readSecretState(SERVER)!;
  const open = openRequests(s);
  assert.deepEqual(open.map((r) => r.id), ["sr-1"]);
  assert.equal(canTryApply(s, open[0]), true);
  assert.equal(canTryApply({ ...s, canEdit: false }, open[0]), false);
  assert.equal(canTryApply({ ...s, myTenants: ["other"] }, open[0]), false);
});

test("입력 검사 문구에 값이 들어가지 않는다", () => {
  assert.equal(valueProblem(DUMMY), null);
  for (const bad of ["short", " leading-space-1", "line\nbreak-value", "x".repeat(5000)]) {
    const p = valueProblem(bad);
    assert.ok(p);
    assert.equal(p.includes(bad.trim()), false);
  }
  assert.equal(nameProblem("MARKETING_VIDEO_API_KEY"), null);
  assert.ok(nameProblem("lower"));
  assert.deepEqual(parseTenants("acme, beta  acme"), ["acme", "beta"]);
  assert.equal(tenantListProblem("acme, beta"), null);
  assert.ok(tenantListProblem("Bad Tenant"));
});

test("쓰기 응답 문구: 저장·보류·실패", () => {
  assert.equal(writeResultKo({ result: { outcome: "applied", output: {} } }).kind, "done");
  const held = writeResultKo({ result: { outcome: "held", output: { review_id: "rev-9" } } });
  assert.equal(held.kind, "held");
  assert.equal(held.reviewId, "rev-9");
  assert.equal(writeResultKo({ result: { outcome: "execute_rejected" } }).kind, "failed");
  assert.equal(writeResultKo(null).kind, "failed");
});

test("오류 문구는 코드·상태만 보고 정한다", () => {
  assert.match(secretHttpErrorKo(409, "SECRET_STORE_DISABLED"), /꺼져/);
  assert.match(secretHttpErrorKo(403, "SECRET_EDITOR_REQUIRED"), /키 담당/);
  assert.match(secretHttpErrorKo(405), /nginx/);
  assert.match(secretHttpErrorKo(undefined), /닿지/);
});

test("이력 한 줄은 누가·고객사/이름·동작만", () => {
  const line = historyLineKo(readSecretState(SERVER)!.history[0]);
  assert.equal(line, "민지 · acme/MARKETING_VIDEO_API_KEY · 교체 요청");
});

test("계정 프로필: 키 담당 권한 규칙과 본문", () => {
  assert.ok(secretEditorProblem({ id: "a", role: "operator" }, { id: "b", role: "member" }));
  assert.ok(secretEditorProblem({ id: "a", role: "admin" }, { id: "a", role: "admin" }));
  assert.equal(secretEditorProblem({ id: "a", role: "operator" }, { id: "a", role: "operator" }), null);
  assert.deepEqual(updateBody({ secret_editor: true, secret_tenants: [" acme ", "acme", ""] }), { secret_editor: true, secret_tenants: ["acme"] });
});
