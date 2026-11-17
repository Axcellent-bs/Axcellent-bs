// node --test로 돈다. DejaFix 연결 카드(DejafixConnect.tsx)가 그리는 값: 연결 여부·현재 장애 n건은 서버 확인 결과에서만.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ToolConnection, ToolField } from "../../lib/toolConnections";
import { dejafixView, formActions, formReducer, incidentCount, initForm } from "../../lib/toolForms.ts";

const dj = (extra: Partial<ToolConnection> = {}): ToolConnection => ({
  id: "dj1", kind: "dejafix", name: "DejaFix", config: { url: "http://10.0.0.5:18000", username: "viewer" }, secrets_set: { password: "abcd••••" },
  checked_at: "2026-11-17T01:00:00Z", check_ok: true, check_detail: "로그인: 로그인했어요; 장애 목록: 현재 장애 3건", updated_at: null, updated_by: null, ...extra,
});
const FIELDS: ToolField[] = [
  { key: "url", label: "DejaFix 백엔드 주소", secret: false, required: true, help: "" },
  { key: "username", label: "viewer 계정 이름", secret: false, required: true, help: "" },
  { key: "password", label: "비밀번호", secret: true, required: true, help: "" },
];

test("연결이 없으면 '안 됨' (주소·계정 칸을 그린다)", () => {
  assert.deepEqual(dejafixView([]), { state: "none" });
  const s = initForm("dejafix", FIELDS);
  assert.deepEqual(Object.keys(s.config), ["url", "username"]);
  assert.deepEqual(Object.keys(s.secrets), ["password"]);
});

test("연결됨: 현재 장애 n건과 마지막 확인은 확인 결과에서", () => {
  const v = dejafixView([dj()]);
  assert.equal(v.state, "connected");
  if (v.state !== "connected") return;
  assert.equal(v.incidents, 3);
  assert.equal(v.checkedAt, "2026-11-17T01:00:00Z");
  assert.equal(v.status.label, "연결됨");
});

test("장애 수가 문구에 없거나 확인이 실패하면 숫자를 지어내지 않는다", () => {
  assert.equal(incidentCount(null), null);
  assert.equal(incidentCount("로그인: 로그인했어요"), null);
  assert.equal(incidentCount("장애 목록: 현재 장애 1,204건"), 1204);
  assert.equal(incidentCount("장애 목록: 현재 장애 0건"), 0);
  const failed = dejafixView([dj({ check_ok: false, check_detail: "로그인: HTTP 401" })]);
  assert.equal(failed.state === "connected" && failed.incidents, null);
  assert.equal(failed.state === "connected" && failed.status.label, "확인 실패");
  const never = dejafixView([dj({ check_ok: null, checked_at: null, check_detail: null })]);
  assert.equal(never.state === "connected" && never.incidents, null);
  assert.equal(never.state === "connected" && never.status.label, "확인 전");
});

test("서버가 남기는 확인 문구 그대로에서 장애 수를 읽는다 (tool_checks.py _dejafix_checks → _summary)", () => {
  // 모두 통과하면 check_detail = "로그인: 로그인했어요; 장애 목록: 현재 장애 {len(rows)}건"
  assert.equal(incidentCount("로그인: 로그인했어요; 장애 목록: 현재 장애 7건"), 7);
  // 실패하면 실패 항목만 남아 숫자가 없다
  assert.equal(incidentCount("장애 목록: HTTP 500"), null);
  assert.equal(incidentCount("로그인: 아이디나 비밀번호가 맞지 않아요"), null);
});

test("관리자만 확인·저장", () => {
  const s = initForm("dejafix", FIELDS);
  assert.deepEqual(formActions(false, s), []);
  assert.equal(formActions(true, s).length, 2);
});

test("저장 뒤 비밀번호 칸은 비워진다", () => {
  let s = formReducer(initForm("dejafix", FIELDS), { type: "secret", key: "password", value: "s3cret-pass" });
  s = formReducer(s, { type: "saved" });
  assert.equal(s.secrets.password, "");
});
