// node --test로 돈다 (npm test). 도구 연결 › 내 연결(개인 MCP 연결)의 순수 계산.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MCP_AGENTS, TOKEN_PLACEHOLDER, mcpSetup, mineStateView, readIssued, readMine, shareBody, shareUsageKo, toggleAgent,
} from "./personalMcp.ts";

const RAW = {
  company: { id: "catbooks", name: "캣북스" },
  agents_available: ["orchestrator", "support", "finance", "data", "hr", "marketing", "planning", "dev", "infra", "archive"],
  state: "connected",
  can_save: true,
  token: { issued: true, hint: "Ab3d", issued_at: "2026-11-20T01:00:00Z" },
  settings: { agents: ["planning", "dev", "nope"], monthly_share_percent: 20, updated_at: null },
  usage: {
    month: "2026-11", recording: true, used_tokens: 500, budget_tokens: 10000, share_percent: 20,
    limit_tokens: 2000, percent_of_budget: 5, percent_of_limit: 25, partial: false,
  },
};

test("AI 직원 목록은 서비스 목록의 10명과 같다(이름은 화면 이름)", () => {
  assert.deepEqual(MCP_AGENTS.map((a) => a.id), ["orchestrator", "support", "finance", "data", "hr", "marketing", "planning", "dev", "infra", "archive"]);
  assert.equal(MCP_AGENTS.find((a) => a.id === "dev")?.name, "개발 AI");
});

test("서버 응답 읽기: 모르는 AI 직원은 버리고, 토큰은 끝 4자리만", () => {
  const m = readMine(RAW);
  assert.ok(m);
  assert.deepEqual(m.settings.agents, ["planning", "dev"]);
  assert.deepEqual(m.token, { issued: true, hint: "Ab3d", issued_at: "2026-11-20T01:00:00Z" });
  assert.equal(m.usage?.limit_tokens, 2000);
  assert.equal(readMine(null), null);
  assert.equal(readMine({ state: "x" }), null);
  assert.ok(!JSON.stringify(m).includes("axm_"));
});

test("상태 칩: 연결됨 ••••끝4 / 연결 안 됨 / 회사 없음", () => {
  const m = readMine(RAW)!;
  assert.deepEqual(mineStateView(m), { tone: "ok", label: "연결됨 ••••Ab3d" });
  assert.deepEqual(mineStateView({ ...m, token: { issued: false, hint: "", issued_at: null } }), { tone: "off", label: "연결 안 됨" });
  assert.equal(mineStateView({ ...m, state: "no_company" }).label, "회사 없음");
});

test("발급 응답: 원문은 axm_으로 시작할 때만 받는다", () => {
  assert.deepEqual(readIssued({ token: "axm_xyz1234", hint: "1234" }), { token: "axm_xyz1234", hint: "1234" });
  assert.equal(readIssued({ token: "nope" }), null);
  assert.equal(readIssued(null), null);
});

test("내 몫 %: 비우면 제한 없음, 1~100 정수만", () => {
  assert.deepEqual(shareBody({ share: "", agents: ["dev", "planning"] }), { agents: ["planning", "dev"], monthly_share_percent: null });
  assert.deepEqual(shareBody({ share: " 20 ", agents: [] }), { agents: [], monthly_share_percent: 20 });
  for (const bad of ["0", "101", "1.5", "-3", "abc"]) assert.equal(shareBody({ share: bad, agents: [] }), "invalid");
});

test("사용량 문장: 실제 센 값만, 예산이 없으면 % 대신 그렇게 말한다", () => {
  const m = readMine(RAW)!;
  assert.equal(shareUsageKo(m.usage), "이번 달 500 / 2,000 토큰 (내 몫의 25%) · 회사 예산의 5%");
  const noBudget = { ...m.usage!, budget_tokens: null, limit_tokens: null, percent_of_budget: null, percent_of_limit: null };
  assert.equal(shareUsageKo(noBudget), "이번 달 500 토큰 · 회사 월 예산이 없어 % 한도는 걸리지 않아요");
  const noShare = { ...m.usage!, share_percent: null, limit_tokens: null, percent_of_limit: null };
  assert.equal(shareUsageKo(noShare), "이번 달 500 토큰 · 회사 예산의 5% · 내 몫 제한 없음");
  assert.match(shareUsageKo({ ...m.usage!, recording: false }), /기록이 꺼져/);
  assert.equal(shareUsageKo(null), "사용량을 받지 못했어요");
});

test("연결 방법: 도구별 설정에 MCP 주소와 토큰 자리(axm_…)만, 원문은 넣지 않는다", () => {
  const url = "http://localhost:8111/api/planning/mcp";
  const claude = mcpSetup("claude", url);
  assert.equal(claude.length, 2);
  assert.equal(claude[0].text, `claude mcp add --transport http axcellent-dev ${url} --header "Authorization: Bearer ${TOKEN_PLACEHOLDER}"`);
  const codex = mcpSetup("codex", url);
  assert.ok(codex.some((s) => s.text === `codex mcp add axcellent-dev --url ${url} --bearer-token-env-var AXCELLENT_MCP_TOKEN`));
  assert.ok(codex.some((s) => s.text.includes(`setx AXCELLENT_MCP_TOKEN "${TOKEN_PLACEHOLDER}"`)));
  const cursor = mcpSetup("cursor", url);
  const json = JSON.parse(cursor.find((s) => s.text.startsWith("{"))!.text);
  assert.deepEqual(json, { mcpServers: { "axcellent-dev": { url, headers: { Authorization: "Bearer ${env:AXCELLENT_MCP_TOKEN}" } } } });
  assert.equal(TOKEN_PLACEHOLDER, "axm_…");
});

test("AI 직원 고르기는 목록 순서를 지킨다", () => {
  assert.deepEqual(toggleAgent(["dev"], "planning"), ["planning", "dev"]);
  assert.deepEqual(toggleAgent(["planning", "dev"], "dev"), ["planning"]);
});
