// node --test로 돈다 (npm test). 회사 AI 연결 화면(공통 AI 도구 연결·AI 연결)의 순수 계산.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  OTHER_COLOR, PIE_COLORS, agentLabel, budgetKo, limitLineKo, parseLimitPercent, canApprove, historyLineKo, keyHintKo, parseBudget, percentKo, pieSlices, providerToSave,
  providerView, readCompanyAi, readStatus, readUsage, ringPath, saveBlockReason, settingsBody, keyResultKo, stateView, videoView,
  type CompanyAiAdmin, type UsageDepartment,
} from "./companyAi.ts";

test("공급자 표시: 서버가 판별 규칙을 아직 두지 않아 모델 이름 앞부분은 표시용으로만 쓴다", () => {
  assert.deepEqual(providerView("claude-sonnet-5-5", "auto", ""), { label: "Anthropic (모델 이름으로 짐작)", needsUrl: false });
  assert.deepEqual(providerView("gpt-5-mini", "auto", ""), { label: "OpenAI (모델 이름으로 짐작)", needsUrl: false });
  assert.deepEqual(providerView("o3", "auto", ""), { label: "OpenAI (모델 이름으로 짐작)", needsUrl: false });
  assert.deepEqual(providerView("gemini-3-pro", "auto", ""), { label: "Google (모델 이름으로 짐작)", needsUrl: false });
  // 목록에 없으면 주소 칸을 연다
  assert.deepEqual(providerView("qwen3.5-4b", "auto", ""), { label: "목록에 없는 모델 · OpenAI 호환 주소를 넣어 주세요", needsUrl: true });
  assert.deepEqual(providerView("", "auto", ""), { label: "모델 이름을 넣으면 공급자를 보여 줘요", needsUrl: false });
  // 서버가 돌려준 provider가 auto가 아니면 그대로 쓴다
  assert.deepEqual(providerView("claude-x", "openai_compatible", "https://llm.example/v1"), { label: "OpenAI 호환 주소", needsUrl: true });
  assert.deepEqual(providerView("anything", "bedrock", ""), { label: "bedrock", needsUrl: false });
  // 주소를 이미 넣었으면 알려진 모델이어도 주소 칸을 보인다
  assert.equal(providerView("claude-x", "auto", "https://proxy.example/v1").needsUrl, true);
});

test("저장할 provider: 주소가 있으면 openai_compatible, 없으면 auto. 서버가 정한 다른 공급자는 그대로", () => {
  assert.equal(providerToSave("claude-x", "", "auto"), "auto");
  assert.equal(providerToSave("qwen", "https://llm.example/v1", "auto"), "openai_compatible");
  assert.equal(providerToSave("claude-x", "", "openai_compatible"), "auto");
  assert.equal(providerToSave("x", "", "bedrock"), "bedrock");
});

test("월 예산 입력: 비우면 예산 없음, 쉼표 허용, 음수·소수·글자는 거절", () => {
  assert.equal(parseBudget(""), null);
  assert.equal(parseBudget(" 5,000,000 "), 5_000_000);
  assert.equal(parseBudget("0"), 0);
  assert.equal(parseBudget("-1"), "invalid");
  assert.equal(parseBudget("1.5"), "invalid");
  assert.equal(parseBudget("abc"), "invalid");
  assert.equal(parseBudget("10000000000001"), "invalid");
});

test("사용 % 문구와 예산 문구", () => {
  assert.equal(percentKo(null), "—");
  assert.equal(percentKo(42.04), "42%");
  assert.equal(percentKo(0.4), "0.4%");
  assert.equal(percentKo(130), "130%");
  assert.equal(budgetKo({ used_tokens: 420_000, budget_tokens: 5_000_000, percent: 8.4 }), "이번 달 사용 8.4% (420,000 / 5,000,000 토큰)");
  assert.equal(budgetKo({ used_tokens: 1200, budget_tokens: null, percent: null }), "예산 없음 · 이번 달 1,200 토큰 사용");
  assert.equal(budgetKo(null), "이번 달 사용량을 받지 못했어요");
});

test("상태 문구: 서버 상태 6가지를 말로", () => {
  assert.deepEqual(stateView("connected"), { tone: "ok", label: "연결됨", detail: "회사 AI 키로 AI 직원이 일해요." });
  assert.equal(stateView("trial").tone, "wait");
  assert.equal(stateView("trial").label, "체험 중");
  assert.equal(stateView("no_key").label, "키 없음");
  assert.equal(stateView("trial_exhausted").label, "체험 한도 다 씀");
  assert.equal(stateView("trial_exhausted").tone, "bad");
  assert.equal(stateView("budget_exhausted").label, "예산 다 씀");
  assert.equal(stateView("no_company").tone, "off");
  assert.equal(stateView("weird").label, "알 수 없는 상태 (weird)");
});

test("상태 응답 읽기: 모르는 칸은 버리고 빠진 칸은 비운다", () => {
  const s = readStatus({
    company: { id: "catbooks", name: "캣북스" }, state: "trial", model: "claude-x", key: { has_key: false }, month: "2026-11",
    usage: { recording: true, used_tokens: 420000, budget_tokens: null, percent: null, partial: false },
    trial: { available: true, limit_tokens: 1000000, used_tokens: 420000, percent: 42 },
    video: { enabled: false, provider: "" }, coder: { mode: "company" },
    notices: [{ at: "2026-11-20T01:00:00+00:00", by: "민지", action: "key_add", name: "LLM_API_KEY", hint: "0001" }, "bad"],
    secret: "should-not-be-read",
  });
  assert.ok(s);
  assert.equal(s.state, "trial");
  assert.equal(s.trial?.percent, 42);
  assert.equal(s.notices.length, 1);
  assert.equal((s as unknown as Record<string, unknown>).secret, undefined);
  assert.equal(readStatus(null), null);
  assert.equal(readStatus({ state: 3 }), null);
});

test("사용량 응답 읽기", () => {
  const u = readUsage({
    company: null, month: "2026-11", recording: true, total_tokens: 30, budget_tokens: 100, percent: 30, partial: false, media: null,
    departments: [{ department: "dev", input_tokens: 10, output_tokens: 10, calls: 2, total_tokens: 20, percent_of_budget: 20, share_percent: 66.7 }, { department: 1 }],
  });
  assert.ok(u);
  assert.equal(u.departments.length, 1);
  assert.equal(u.departments[0].total_tokens, 20);
  assert.equal(readUsage("x"), null);
});

const dept = (department: string, total_tokens: number): UsageDepartment =>
  ({ department, input_tokens: total_tokens, output_tokens: 0, calls: 1, total_tokens, percent_of_budget: null, share_percent: null });

test("원그래프 조각: 상위 5개 + 나머지는 회색 기타, 0 토큰은 뺀다, 비율은 토큰으로 다시 셈", () => {
  const rows = [dept("dev", 97), dept("data", 90), dept("marketing", 71), dept("support", 62), dept("orchestrator", 39), dept("hr", 30), dept("finance", 31), dept("safety", 0)];
  const s = pieSlices(rows, false);
  assert.equal(s.length, 6);
  assert.deepEqual(s.map((x) => x.label), ["개발 AI", "데이터분석 AI", "마케팅 AI", "고객지원 AI", "총괄 AI", "기타 2개"]);
  assert.deepEqual(s.map((x) => x.color), [...PIE_COLORS.light, OTHER_COLOR]);
  assert.equal(s[5].tokens, 61);
  const total = 97 + 90 + 71 + 62 + 39 + 61;
  assert.equal(s[0].percent, Math.round((97 * 1000) / total) / 10);
  assert.ok(Math.abs(s.reduce((a, x) => a + x.fraction, 0) - 1) < 1e-9);
  // 서버 순서와 상관없이 많이 쓴 순
  assert.equal(pieSlices([dept("a", 1), dept("b", 5)], false)[0].key, "b");
  // 다크 색
  assert.deepEqual(pieSlices(rows, true).slice(0, 5).map((x) => x.color), PIE_COLORS.dark);
  // 여섯 개여도 상위 5 + 기타 1개
  assert.equal(pieSlices(rows.slice(0, 6), false)[5].label, "기타 1개");
  assert.deepEqual(pieSlices([], false), []);
  assert.deepEqual(pieSlices([dept("dev", 0)], false), []);
});

test("색약 구분 순서: 파랑·주황·청록·노랑·분홍", () => {
  assert.deepEqual(PIE_COLORS.light, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]);
  assert.deepEqual(PIE_COLORS.dark, ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]);
});

test("고리 조각 경로: 반 바퀴 넘으면 큰 호, 한 조각뿐이면 두 반쪽으로 닫는다", () => {
  const half = ringPath(60, 60, 56, 36, 0, Math.PI);
  assert.match(half, /^M/);
  assert.match(ringPath(60, 60, 56, 36, 0, Math.PI * 1.5), / 0 1 1 /);
  assert.match(ringPath(60, 60, 56, 36, 0, Math.PI / 2), / 0 0 1 /);
  const full = ringPath(60, 60, 56, 36, 0, Math.PI * 2);
  assert.equal((full.match(/A/g) ?? []).length, 4);
});

test("AI 직원 이름", () => {
  assert.equal(agentLabel("support"), "고객지원 AI");
  assert.equal(agentLabel("orchestrator"), "총괄 AI");
  assert.equal(agentLabel(""), "부서 없음");
  assert.equal(agentLabel("newdept"), "newdept");
});

test("키 끝 글자: 끝 4자리만, 없으면 대시", () => {
  assert.equal(keyHintKo("ab12"), "••••ab12");
  assert.equal(keyHintKo(""), "—");
});

const adminRaw = (over: Record<string, unknown> = {}) => ({
  company: { id: "catbooks", name: "캣북스" },
  secret_store: { enabled: true, reason: "" },
  can_save: true,
  settings: { model: "claude-x", provider: "auto", base_url: "", monthly_budget_tokens: 5000000, coder: { mode: "company", model: "", base_url: "" }, video: { provider: "", enabled: false }, embed_model: "", embed_base_url: "", updated_at: null, updated_by: "" },
  keys: [
    { name: "LLM_API_KEY", has_key: true, hint: "0001", updated_at: null, updated_by: "민지" },
    { name: "DEV_CODER_LLM_API_KEY", has_key: false, hint: "", updated_at: null, updated_by: null },
    { name: "MARKETING_VIDEO_API_KEY", has_key: false, hint: "", updated_at: null, updated_by: null },
  ],
  requests: [{ id: "r1", op: "replace", name: "LLM_API_KEY", hint: "0002", reason: "", requested_by: "민지", requested_at: "2026-11-20T01:00:00+00:00" }],
  approval: { needed: true, admins: 2 },
  usage_recording: true,
  history: [{ at: "2026-11-20T01:00:00+00:00", by: "민지", action: "request_replace", name: "LLM_API_KEY", hint: "0002", request_id: "r1" }],
  ...over,
});

test("관리자 응답 읽기: 키는 끝 글자만 남는다", () => {
  const a = readCompanyAi(adminRaw()) as CompanyAiAdmin;
  assert.ok(a);
  assert.equal(a.keys.LLM_API_KEY.hint, "0001");
  assert.equal(a.keys.DEV_CODER_LLM_API_KEY.has_key, false);
  assert.equal(a.requests.length, 1);
  assert.equal(a.settings.monthly_budget_tokens, 5000000);
  assert.equal(readCompanyAi({}), null);
});

test("저장 막는 이유: 회사 없음 → 키 저장소 꺼짐 → 저장 위치 없음 순", () => {
  assert.equal(saveBlockReason(readCompanyAi(adminRaw())!), null);
  assert.equal(saveBlockReason(readCompanyAi(adminRaw({ company: null, can_save: false }))!), "회사 연결에서 회사를 먼저 정해 주세요.");
  assert.equal(
    saveBlockReason(readCompanyAi(adminRaw({ secret_store: { enabled: false, reason: "키 저장소가 꺼져 있어요 (서버 설정 AX_SECRET_STORE=db 필요)" }, can_save: false }))!),
    "키 저장소가 꺼져 있어요 (서버 설정 AX_SECRET_STORE=db 필요)",
  );
  assert.equal(saveBlockReason(readCompanyAi(adminRaw({ can_save: false }))!), "회사 AI 설정을 둘 곳이 없어요 (서버 설정 AX_COMPANY_AI_DIR 필요).");
});

test("저장 본문: 서버가 받는 칸만 (updated_at·updated_by는 보내지 않는다)", () => {
  const a = readCompanyAi(adminRaw())!;
  const body = settingsBody(a.settings, { model: " qwen ", base_url: " https://llm.example/v1 ", monthly_budget_tokens: null });
  assert.deepEqual(Object.keys(body).sort(), ["base_url", "coder", "embed_base_url", "embed_model", "model", "monthly_budget_tokens", "monthly_limit_percent", "provider", "video"]);
  assert.equal(body.model, "qwen");
  assert.equal(body.base_url, "https://llm.example/v1");
  assert.equal(body.provider, "openai_compatible");
  assert.equal(body.monthly_budget_tokens, null);
  const coder = settingsBody(a.settings, { coder: { mode: "separate", model: "gpt-5", base_url: "" } });
  assert.equal(coder.model, "claude-x");
  assert.equal(coder.provider, "auto");
  assert.deepEqual(coder.coder, { mode: "separate", model: "gpt-5", base_url: "" });
});

test("승인 버튼: 본인 요청에는 없다", () => {
  const r = readCompanyAi(adminRaw())!.requests[0];
  assert.equal(canApprove(r, "민지"), false);
  assert.equal(canApprove(r, "태윤"), true);
});

test("키 저장 결과 문구", () => {
  assert.equal(keyResultKo({ applied: true, op: "add", hint: "0001" }, 2), "처음 등록이라 바로 적용했어요. 저장된 키 ••••0001");
  assert.equal(keyResultKo({ applied: true, op: "replace", hint: "0002" }, 1), "관리자가 한 명뿐이라 바로 바꿨고 회원 모두에게 알렸어요. 저장된 키 ••••0002");
  assert.equal(keyResultKo({ applied: true, op: "delete", hint: "" }, 1), "관리자가 한 명뿐이라 바로 지웠고 회원 모두에게 알렸어요.");
  assert.equal(keyResultKo({ applied: false, op: "replace", hint: "0002" }, 2), "교체 요청을 남겼어요(••••0002). 다른 관리자가 승인하면 적용돼요.");
  assert.equal(keyResultKo({ applied: false, op: "delete", hint: "" }, 2), "삭제 요청을 남겼어요. 다른 관리자가 승인하면 적용돼요.");
});

test("변경 기록 한 줄: 누가·무엇, 키는 끝 4자리", () => {
  assert.equal(historyLineKo({ at: "", by: "민지", action: "key_add", name: "LLM_API_KEY", hint: "0001" }), "민지 · 회사 AI 키 등록 ••••0001");
  assert.equal(historyLineKo({ at: "", by: "태윤", action: "key_replace", name: "DEV_CODER_LLM_API_KEY", hint: "0002", requested_by: "민지" }), "태윤 · 코딩 에이전트 키 교체 ••••0002 (민지 요청 승인)");
  assert.equal(historyLineKo({ at: "", by: "민지", action: "key_delete", name: "MARKETING_VIDEO_API_KEY", hint: "0003", single_admin: true }), "민지 · 영상 생성 키 삭제 ••••0003 (관리자 한 명이라 바로 적용)");
  assert.equal(historyLineKo({ at: "", by: "민지", action: "request_replace", name: "LLM_API_KEY", hint: "0002" }), "민지 · 회사 AI 키 교체 요청 ••••0002");
  assert.equal(historyLineKo({ at: "", by: "태윤", action: "reject", name: "LLM_API_KEY" }), "태윤 · 회사 AI 키 요청 거절");
  assert.equal(historyLineKo({ at: "", by: "민지", action: "settings", changed: ["model", "monthly_budget_tokens", "coder"] }), "민지 · 설정 변경: 모델, 월 예산, 코딩 에이전트");
});

test("영상 생성 상태: 키가 있어야 켜진다", () => {
  assert.deepEqual(videoView(false, true), { tone: "wait", label: "꺼짐 · 키 필요" });
  assert.deepEqual(videoView(true, false), { tone: "off", label: "꺼짐" });
  assert.deepEqual(videoView(true, true), { tone: "ok", label: "켜짐" });
});

test("상한 %: 비우면 100(기본), 1~100 정수만. 저장 본문에 실린다", () => {
  assert.equal(parseLimitPercent(""), null);
  assert.equal(parseLimitPercent(" 80 "), 80);
  assert.equal(parseLimitPercent("80%"), 80);
  for (const bad of ["0", "101", "1.5", "x"]) assert.equal(parseLimitPercent(bad), "invalid");
  const a = readCompanyAi(adminRaw())!;
  assert.equal(a.settings.monthly_limit_percent, null);
  assert.equal(settingsBody(a.settings, { monthly_limit_percent: 80 }).monthly_limit_percent, 80);
  assert.equal(settingsBody(a.settings, {}).monthly_limit_percent, null);
  const b = readCompanyAi(adminRaw({ settings: { ...adminRaw().settings, monthly_limit_percent: 70 } }))!;
  assert.equal(b.settings.monthly_limit_percent, 70);
  assert.equal(historyLineKo({ at: "", by: "민지", action: "settings", changed: ["monthly_limit_percent"] }), "민지 · 설정 변경: 상한 %");
});

test("상한선 문구: 예산이 있을 때만, 서버가 준 상한으로", () => {
  assert.equal(limitLineKo({ used_tokens: 10, budget_tokens: 5000000, percent: 0, limit_percent: 80, limit_tokens: 4000000 }), "80%(4,000,000 토큰)에서 멈춰요");
  assert.equal(limitLineKo({ used_tokens: 10, budget_tokens: null, percent: null, limit_percent: 100, limit_tokens: null }), null);
  assert.equal(limitLineKo(null), null);
});
