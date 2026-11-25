// node --test로 돈다. 회원·서버 관리 › "연결한 PC" 칸(ConnectedPcs.tsx)이 그리는 값: 장애 목록에서 호스트 모으기·상태·정렬.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Incident } from "./dejafix";
import { AGENT_DOWN, connectedPcs, fromHosts, pcSummary } from "./connectedPcs.ts";

let seq = 0;
const inc = (host: string | null, title: string, extra: Partial<Incident> = {}): Incident => ({
  id: ++seq, source: "zabbix", zabbix: { eventid: null, triggerid: null, hostid: null }, host: { id: null, name: host },
  title, severity: { level: 3, label: "Average" }, status: "problem", started_at: "2026-11-19T01:00:00Z", resolved_at: null,
  tags: [], analysis: { status: "done", report_id: null, plan_id: null }, ...extra,
});

test("두 목록의 호스트 합집합 · 이름 없는 장애는 뺀다", () => {
  const rows = connectedPcs(
    [inc("web-02", "High CPU", { status: "resolved" }), inc(null, "보강 전"), inc("db-01", "Disk full", { status: "resolved" })],
    [inc("web-02", "High CPU"), inc("pc-kim", "Memory")],
  );
  assert.deepEqual(rows.map((r) => r.name), ["db-01", "pc-kim", "web-02"]);
  // 장애 기록만으로는 연결됐는지 모른다: 응답 없음이 아니면 '확인할 수 없음' (연결됨으로 보지 않는다)
  assert.ok(rows.every((r) => r.state === "unknown" && r.since === null && r.problems === null));
});

test("열린 장애에 agent 응답 없음이 있으면 '응답 없음', 가장 이른 시작 시각부터", () => {
  const rows = connectedPcs(
    [inc("pc-lee", `Windows: ${AGENT_DOWN} (for 3m)`, { started_at: "2026-11-18T23:00:00Z" })],
    [inc("pc-lee", `Windows: ${AGENT_DOWN} (for 3m)`, { started_at: "2026-11-19T02:00:00Z" }), inc("pc-lee", "High CPU", { started_at: "2026-11-18T20:00:00Z" })],
  );
  assert.deepEqual(rows, [{ name: "pc-lee", state: "down", since: "2026-11-18T23:00:00Z", problems: null }]);
});

test("해결된 agent 응답 없음은 지금 상태가 아니다", () => {
  const rows = connectedPcs([inc("pc-park", AGENT_DOWN, { status: "resolved", resolved_at: "2026-11-19T03:00:00Z" })], []);
  assert.deepEqual(rows, [{ name: "pc-park", state: "unknown", since: null, problems: null }]);
});

test("응답 없음 먼저, 그다음 이름순 (장애 기록 기준)", () => {
  const rows = connectedPcs([inc("a-pc", "x", { status: "resolved" }), inc("c-pc", "x", { status: "resolved" })], [inc("z-pc", AGENT_DOWN), inc("b-pc", AGENT_DOWN)]);
  assert.deepEqual(rows.map((r) => `${r.state}:${r.name}`), ["down:b-pc", "down:z-pc", "unknown:a-pc", "unknown:c-pc"]);
});

test("요약: 연결된 수 / 확인할 수 없는 수 / 전체와 막대 폭", () => {
  const rows = fromHosts([
    { id: "1", name: "a", state: "up", problems: 0 },
    { id: "2", name: "b", state: "up", problems: 1 },
    { id: "3", name: "c", state: "down", problems: 2 },
    { id: "4", name: "d", state: "unknown", problems: 0 },
  ]);
  assert.deepEqual(pcSummary(rows), { connected: 2, unknown: 1, total: 4, pct: 50 });
  assert.deepEqual(pcSummary([]), { connected: 0, unknown: 0, total: 0, pct: 0 });
  // 장애 기록 기준이면 연결된 수는 0 (연결됨을 정하지 못한다)
  assert.equal(pcSummary(connectedPcs([inc("a", "x"), inc("c", AGENT_DOWN)], [])).connected, 0);
});

test("호스트 목록(DejaFix #7): 상태·열린 Problem 수 그대로, 응답 없음·확인할 수 없음·연결됨 순", () => {
  const rows = fromHosts([
    { id: "1", name: "web-01", state: "up", problems: 0 },
    { id: "2", name: "office-pc", state: "unknown", problems: 0 },
    { id: "3", name: "db-01", state: "down", problems: 2 },
    { id: "4", name: "api-01", state: "up", problems: 3 },
  ]);
  assert.deepEqual(rows.map((r) => `${r.state}:${r.name}:${r.problems}`), [
    "down:db-01:2", "unknown:office-pc:0", "up:api-01:3", "up:web-01:0",
  ]);
});

test("회사 연결 지도의 사내 PC 칸: 연결 전·못 읽음·읽기 전에는 숫자를 만들지 않는다", async () => {
  const { pcMapNode } = await import("./connectedPcs.ts");
  assert.deepEqual(pcMapNode(null), { value: "확인 중…", tone: "off", note: "" });
  assert.equal(pcMapNode({ notConnected: true }).value, "DejaFix 연결 전");
  assert.equal(pcMapNode({ error: "HTTP 500" }).tone, "bad");
  const hosts = (states: ("up" | "down" | "unknown")[]) =>
    ({ rows: fromHosts(states.map((st, i) => ({ id: String(i), name: `pc${i}`, state: st, problems: 0 }))), checkedAt: "", fromIncidents: false });
  assert.deepEqual(pcMapNode(hosts(["up", "up"])), { value: "2 / 2대", tone: "ok", note: "모두 연결됨" });
  assert.deepEqual(pcMapNode(hosts(["up", "down"])), { value: "1 / 2대", tone: "wait", note: "1대 응답 없음" });
  assert.deepEqual(pcMapNode(hosts(["up", "unknown"])), { value: "1 / 2대", tone: "off", note: "1대 확인할 수 없음" });
  assert.equal(pcMapNode(hosts([])).value, "0대");
});
