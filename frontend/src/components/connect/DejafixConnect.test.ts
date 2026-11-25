// node --test로 돈다. DejaFix 연결 카드(DejafixConnect.tsx)가 그리는 값: 연결 출처·응답 여부는 프록시 /status에서만.
// 같이 설치된 DejaFix가 없는 회사는 관리자가 화면에서 주소·조회 계정을 넣어 연결한다(보관함, 11/25).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ToolConnection } from "../../lib/toolConnections";
import { dejafixCard, dejafixSummary, dejafixView, parseDejafixStatus, type DejafixStatus } from "../../lib/toolForms.ts";

const dj = (extra: Partial<ToolConnection> = {}): ToolConnection => ({
  id: "dj1", kind: "dejafix", name: "DejaFix", config: { url: "http://10.0.0.5:18000", username: "viewer" }, secrets_set: { password: "••••" },
  checked_at: "2026-11-17T01:00:00Z", check_ok: true, check_detail: "로그인: 로그인했어요; 장애 목록: 현재 장애 3건", updated_at: null, updated_by: null, ...extra,
});

test("보관함에 저장한 연결이 없으면 none", () => {
  assert.deepEqual(dejafixView([]), { state: "none" });
});

test("보관함에 저장한 연결: 상태는 서버 확인 결과에서", () => {
  const v = dejafixView([dj()]);
  assert.equal(v.state === "connected" && v.conn.id, "dj1");
  assert.equal(v.state === "connected" && v.status.label, "연결됨");
  const failed = dejafixView([dj({ check_ok: false, check_detail: "로그인: HTTP 401" })]);
  assert.equal(failed.state === "connected" && failed.status.label, "확인 실패");
  const never = dejafixView([dj({ check_ok: null, checked_at: null, check_detail: null })]);
  assert.equal(never.state === "connected" && never.status.label, "확인 전");
});

// 연결 출처는 프록시 GET /status (builtin = 같이 설치된 DejaFix, custom = 회사가 화면에서 연결한 DejaFix, none = 연결 전)
const st = (source: DejafixStatus["source"], reachable: boolean | null = true): DejafixStatus => ({ source, reachable, detail: reachable === false ? "HTTP 500" : "응답해요" });

test("같이 설치된 DejaFix(builtin): 보관함이 비어도 '연결 전'이 아니고 입력 칸 없이 연결됨", () => {
  const card = dejafixCard(st("builtin"), []);
  assert.deepEqual(card, { mode: "builtin", live: { tone: "ok", label: "응답함" }, detail: "응답해요" });
  assert.deepEqual(dejafixSummary(card), { tone: "ok", label: "AXcellent와 같이 설치된 DejaFix에 연결됨" });
  const down = dejafixCard(st("builtin", false), []);
  assert.equal(dejafixSummary(down).tone, "bad");
  assert.equal(down.mode === "builtin" && down.detail, "HTTP 500");
});

test("builtin이면 보관함에 남은 연결은 프록시가 쓰지 않으니 카드에 싣지 않는다", () => {
  const card = dejafixCard(st("builtin"), [dj({ secrets_set: {} })]);
  assert.equal(card.mode, "builtin");
  assert.equal("saved" in card, false);
});

test("회사 DejaFix(custom): 저장한 연결(바꾸기·끊기에 쓸 id)과 지금 응답 여부", () => {
  const card = dejafixCard(st("custom"), [dj()]);
  assert.equal(card.mode, "custom");
  if (card.mode !== "custom") return;
  assert.equal(card.saved.conn.id, "dj1");
  assert.deepEqual(dejafixSummary(card), { tone: "ok", label: "회사 DejaFix에 연결됨" });
  assert.equal(dejafixSummary(dejafixCard(st("custom", false), [dj()])).label, "회사 DejaFix 응답 없음");
});

test("none: 연결 전 (관리자가 화면에서 연결한다)", () => {
  const card = dejafixCard(st("none", null), []);
  assert.deepEqual(card, { mode: "none" });
  assert.equal(dejafixSummary(card).label, "연결 전");
});

test("상태를 못 읽으면 '확인할 수 없음' (연결됐다고도 안 됐다고도 하지 않는다)", () => {
  assert.deepEqual(dejafixCard(null, []), { mode: "unknown", saved: null });
  assert.equal(dejafixSummary(dejafixCard(null, [])).label, "확인할 수 없음");
  // 저장한 연결은 목록에서 읽은 그대로 넘긴다 (끊기를 보여 준다)
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
