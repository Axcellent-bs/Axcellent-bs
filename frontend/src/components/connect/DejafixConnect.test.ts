// node --test로 돈다. DejaFix 연결 카드(DejafixConnect.tsx)가 그리는 값: 연결 여부·현재 장애 n건은 서버 확인 결과에서만.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ToolConnection, ToolField } from "../../lib/toolConnections";
import { dejafixCard, dejafixSummary, dejafixView, formActions, formReducer, incidentCount, initForm, parseDejafixStatus, type DejafixStatus } from "../../lib/toolForms.ts";

const dj = (extra: Partial<ToolConnection> = {}): ToolConnection => ({
  id: "dj1", kind: "dejafix", name: "DejaFix", config: { url: "http://10.0.0.5:18000", username: "viewer" }, secrets_set: { password: "••••" },
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

// 연결 출처는 프록시 GET /status (builtin = 같이 설치된 DejaFix, custom = 화면에서 저장한 다른 DejaFix, none = 없음)
const st = (source: DejafixStatus["source"], reachable: boolean | null = true): DejafixStatus => ({ source, reachable, detail: reachable === false ? "HTTP 500" : "응답해요" });

test("같이 설치된 DejaFix(builtin): 보관함이 비어도 '연결 전'이 아니고 입력 칸 없이 연결됨", () => {
  const card = dejafixCard(st("builtin"), []);
  assert.equal(card.mode, "builtin");
  assert.equal(card.mode === "builtin" && card.saved, null);
  assert.deepEqual(dejafixSummary(card), { tone: "ok", label: "AXcellent와 같이 설치된 DejaFix에 연결됨" });
  const down = dejafixCard(st("builtin", false), []);
  assert.equal(dejafixSummary(down).tone, "bad");
  assert.equal(down.mode === "builtin" && down.detail, "HTTP 500");
});

test("builtin인데 보관함에 덜 채운 연결이 남아 있으면 고급 영역에서 고치게 같이 준다", () => {
  const card = dejafixCard(st("builtin"), [dj({ secrets_set: {} })]);
  assert.equal(card.mode === "builtin" && card.saved?.conn.id, "dj1");
});

test("다른 DejaFix(custom): 저장한 연결과 지금 응답 여부", () => {
  const card = dejafixCard(st("custom"), [dj()]);
  assert.equal(card.mode, "custom");
  if (card.mode !== "custom") return;
  assert.equal(card.saved.conn.config.url, "http://10.0.0.5:18000");
  assert.equal(card.saved.incidents, 3);
  assert.deepEqual(dejafixSummary(card), { tone: "ok", label: "다른 DejaFix에 연결됨" });
  assert.equal(dejafixSummary(dejafixCard(st("custom", false), [dj()])).label, "다른 DejaFix 응답 없음");
});

test("none: 같이 설치된 DejaFix가 설정되지 않음", () => {
  const card = dejafixCard(st("none", null), []);
  assert.deepEqual(card, { mode: "none" });
  assert.equal(dejafixSummary(card).label, "같이 설치된 DejaFix 설정 안 됨");
});

test("상태를 못 읽으면 '확인할 수 없음' (연결됐다고도 안 됐다고도 하지 않는다)", () => {
  assert.deepEqual(dejafixCard(null, []), { mode: "unknown", saved: null });
  assert.equal(dejafixSummary(dejafixCard(null, [])).label, "확인할 수 없음");
  // 저장한 연결은 목록에서 읽은 그대로 보여 준다
  const withSaved = dejafixCard(null, [dj()]);
  assert.equal(withSaved.mode === "unknown" && withSaved.saved?.conn.id, "dj1");
  // 프록시는 custom인데 목록에 없으면 어느 쪽이 맞는지 모른다
  assert.deepEqual(dejafixCard(st("custom"), []), { mode: "unknown", saved: null });
  // reachable을 모르면 응답 여부도 확인할 수 없음
  assert.equal(dejafixSummary(dejafixCard(st("builtin", null), [])).label, "AXcellent와 같이 설치된 DejaFix · 확인할 수 없음");
});

test("프록시 응답 모양이 다르면 상태를 지어내지 않는다", () => {
  assert.deepEqual(parseDejafixStatus({ source: "builtin", reachable: true, detail: "응답해요" }), { source: "builtin", reachable: true, detail: "응답해요" });
  assert.deepEqual(parseDejafixStatus({ source: "none", reachable: null }), { source: "none", reachable: null, detail: "" });
  assert.equal(parseDejafixStatus(null), null);
  assert.equal(parseDejafixStatus("ok"), null);
  assert.equal(parseDejafixStatus({ source: "vault", reachable: true }), null);
  assert.equal(parseDejafixStatus({ source: "builtin", reachable: "yes" }), null);
  assert.equal(parseDejafixStatus({ source: "builtin" }), null);
  // 개발 서버가 DejaFix로 바로 넘겨 404 본문이 와도 상태로 읽지 않는다
  assert.equal(parseDejafixStatus({ error: { code: "NOT_FOUND" } }), null);
});

test("저장 뒤 비밀번호 칸은 비워진다", () => {
  let s = formReducer(initForm("dejafix", FIELDS), { type: "secret", key: "password", value: "s3cret-pass" });
  s = formReducer(s, { type: "saved" });
  assert.equal(s.secrets.password, "");
});
