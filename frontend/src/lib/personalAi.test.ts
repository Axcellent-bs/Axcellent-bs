// node --test로 돈다 (npm test). 도구 연결 › 내 연결(개인 AI 연결)의 순수 계산.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PERSONAL_AGENTS, mineStateView, personalBody, personalUsageKo, readMine, toggleAgent,
} from "./personalAi.ts";

const RAW = {
  company: { id: "catbooks", name: "캣북스" },
  secret_store: { enabled: true, reason: "" },
  agents_available: ["orchestrator", "support", "finance", "data", "hr", "marketing", "planning", "dev", "infra", "archive"],
  state: "connected",
  can_save: true,
  key: { has_key: true, hint: "0001", updated_at: "2026-11-20T01:00:00Z" },
  settings: { model: "m", provider: "auto", base_url: "", monthly_limit_tokens: 1000, agents: ["planning", "dev", "nope"], updated_at: null },
  usage: { month: "2026-11", recording: true, used_tokens: 250, limit_tokens: 1000, percent: 25, partial: false },
};

test("AI 직원 목록은 서비스 목록의 10명과 같다(이름은 화면 이름)", () => {
  assert.deepEqual(PERSONAL_AGENTS.map((a) => a.id), ["orchestrator", "support", "finance", "data", "hr", "marketing", "planning", "dev", "infra", "archive"]);
  assert.equal(PERSONAL_AGENTS.find((a) => a.id === "infra")?.name, "인프라 AI");
});

test("서버 응답 읽기: 모르는 AI 직원은 버리고, 키는 끝 4자리만", () => {
  const m = readMine(RAW);
  assert.ok(m);
  assert.deepEqual(m.settings.agents, ["planning", "dev"]);
  assert.deepEqual(m.key, { has_key: true, hint: "0001", updated_at: "2026-11-20T01:00:00Z" });
  assert.equal(m.usage?.percent, 25);
  assert.equal(readMine(null), null);
  assert.equal(readMine({ state: "x" }), null);
});

test("상태 문구: 연결됨(끝 4자리)·키 없음·회사 없음", () => {
  const m = readMine(RAW)!;
  assert.deepEqual(mineStateView(m), { tone: "ok", label: "연결됨 ••••0001" });
  assert.equal(mineStateView({ ...m, state: "no_key", key: { has_key: false, hint: "", updated_at: null } }).label, "연결 안 됨");
  assert.equal(mineStateView({ ...m, state: "no_company" }).label, "회사 없음");
});

test("사용량 문구는 서버가 준 숫자만 쓴다(기록 꺼짐·한도 없음 정직하게)", () => {
  assert.equal(personalUsageKo({ month: "2026-11", recording: true, used_tokens: 250, limit_tokens: 1000, percent: 25, partial: false }), "이번 달 내 키 사용 25% (250 / 1,000 토큰)");
  assert.equal(personalUsageKo({ month: "2026-11", recording: true, used_tokens: 250, limit_tokens: null, percent: null, partial: false }), "한도 없음 · 이번 달 내 키 250 토큰 사용");
  assert.equal(personalUsageKo({ month: "2026-11", recording: false, used_tokens: 0, limit_tokens: 1000, percent: null, partial: false }), "사용량 기록이 꺼져 있어 셀 수 없어요 (서버 설정 AX_LLM_USAGE_DB 필요)");
  assert.equal(personalUsageKo(null), "사용량을 받지 못했어요");
});

test("저장 본문: 다듬고, 한도는 정수 토큰, 직원은 목록 순서로", () => {
  assert.deepEqual(personalBody({ model: " m ", base_url: " ", limit: "1,000", agents: ["dev", "planning"] }),
    { model: "m", base_url: "", monthly_limit_tokens: 1000, agents: ["planning", "dev"] });
  assert.deepEqual(personalBody({ model: "m", base_url: "", limit: "", agents: [] }),
    { model: "m", base_url: "", monthly_limit_tokens: null, agents: [] });
  assert.equal(personalBody({ model: "m", base_url: "", limit: "-1", agents: [] }), "invalid");
});

test("직원 고르기 켜고 끄기", () => {
  assert.deepEqual(toggleAgent(["dev"], "planning"), ["planning", "dev"]);
  assert.deepEqual(toggleAgent(["planning", "dev"], "dev"), ["planning"]);
});
