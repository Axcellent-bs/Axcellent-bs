// node --test로 돈다. 회원·서버 관리 › "연결한 PC" 칸(ConnectedPcs.tsx)이 그리는 값: 장애 목록에서 호스트 모으기·상태·정렬.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Incident } from "./dejafix";
import { AGENT_DOWN, connectedPcs, pcSummary } from "./connectedPcs.ts";

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
  assert.ok(rows.every((r) => r.state === "up" && r.since === null));
});

test("열린 장애에 agent 응답 없음이 있으면 '응답 없음', 가장 이른 시작 시각부터", () => {
  const rows = connectedPcs(
    [inc("pc-lee", `Windows: ${AGENT_DOWN} (for 3m)`, { started_at: "2026-11-18T23:00:00Z" })],
    [inc("pc-lee", `Windows: ${AGENT_DOWN} (for 3m)`, { started_at: "2026-11-19T02:00:00Z" }), inc("pc-lee", "High CPU", { started_at: "2026-11-18T20:00:00Z" })],
  );
  assert.deepEqual(rows, [{ name: "pc-lee", state: "down", since: "2026-11-18T23:00:00Z" }]);
});

test("해결된 agent 응답 없음은 지금 상태가 아니다", () => {
  const rows = connectedPcs([inc("pc-park", AGENT_DOWN, { status: "resolved", resolved_at: "2026-11-19T03:00:00Z" })], []);
  assert.deepEqual(rows, [{ name: "pc-park", state: "up", since: null }]);
});

test("응답 없음 먼저, 그다음 이름순", () => {
  const rows = connectedPcs([inc("a-pc", "x", { status: "resolved" }), inc("c-pc", "x", { status: "resolved" })], [inc("z-pc", AGENT_DOWN), inc("b-pc", AGENT_DOWN)]);
  assert.deepEqual(rows.map((r) => `${r.state}:${r.name}`), ["down:b-pc", "down:z-pc", "up:a-pc", "up:c-pc"]);
});

test("요약: 연결된 수 / 전체와 막대 폭", () => {
  const rows = connectedPcs([inc("a", "x"), inc("b", "x"), inc("c", AGENT_DOWN)], []);
  assert.deepEqual(pcSummary(rows), { connected: 2, total: 3, pct: 67 });
  assert.deepEqual(pcSummary([]), { connected: 0, total: 0, pct: 0 });
});
