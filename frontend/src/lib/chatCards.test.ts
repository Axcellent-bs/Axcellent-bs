// node --test로 돈다 (npm test). 데이터분석 1:1 대화 카드의 표시 값·채우기·폴링·버튼 상태만 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  cardInput, competitorsText, doneView, formFromCard, keywordsText, progressKeepPolling, progressPct, reportCode, reportPath, requestCardRows, submitView,
} from "./chatCards.ts";
import { DONE_CARD_EXAMPLE, DONE_CARD_FAILED, REQUEST_CARD_EXAMPLE, REQUEST_CARD_SPARSE } from "./chatCardsExample.ts";
import { withRawCards } from "./chatCards.ts";
import type { ChatCard, ChatMessage } from "./planningData.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { buildInput } from "./trendReportView.ts";

test("의뢰 카드 표시: 타깃 연령, 경쟁사 이름/@아이디·외 N곳, 키워드 외 N개, 프리셋 꼬리표", () => {
  const rows = requestCardRows(REQUEST_CARD_EXAMPLE);
  const by = Object.fromEntries(rows.map((r) => [r.key, r]));
  assert.equal(by.target.value, "여성 20·30대");
  assert.equal(by.competitors.value, "예시 브랜드 A, @example_b, 예시 브랜드 C 외 1곳");
  assert.equal(by.keywords.value, "예시 키워드1, 예시 키워드2 외 1개");
  assert.deepEqual(rows.filter((r) => r.preset).map((r) => r.key), ["target", "format", "competitors"]);
  const sparse = Object.fromEntries(requestCardRows(REQUEST_CARD_SPARSE).map((r) => [r.key, r.value]));
  assert.deepEqual(sparse, { product: "정하지 않음", target: "전체", format: "정하지 않음", competitors: "@example_x", keywords: "없음" });
  assert.equal(competitorsText(["a", "b", "c"], {}), "@a, @b, @c"); // 3개면 "외" 없음
  assert.equal(keywordsText(["k1", "k2"]), "k1, k2");
  assert.equal(competitorsText([], {}), "없음");
});

test("분석 의뢰 넣기 입력: 의뢰문 + 카드 칸 그대로 + human + 대화 id", () => {
  assert.deepEqual(cardInput(REQUEST_CARD_EXAMPLE, "th_1"), {
    request_text: REQUEST_CARD_EXAMPLE.request_text, product: "예시 제품", target_gender: "f", target_ages: ["20", "30"],
    format: "인스타 릴스 30초", competitors: ["example_a", "example_b", "example_c", "example_d"],
    keywords: ["예시 키워드1", "예시 키워드2", "예시 키워드3"], caller: "human", chat_thread_id: "th_1",
  });
});

test("분석 의뢰 탭에서 고치기: 직접 정하기 폼 값 (의뢰문 비움, 프리셋 유지) → 그대로 다시 보낼 수 있다", () => {
  const form = formFromCard(REQUEST_CARD_EXAMPLE);
  assert.equal(form.requestText, "");
  assert.equal(form.preset, "eco_beauty");
  assert.equal(form.gender, "f");
  assert.deepEqual(form.ages, ["20", "30"]);
  assert.equal(form.competitors, "example_a, example_b, example_c, example_d");
  assert.equal(form.keywords, "예시 키워드1, 예시 키워드2, 예시 키워드3");
  const sparse = formFromCard(REQUEST_CARD_SPARSE);
  assert.equal(sparse.gender, "all");
  assert.equal(sparse.product, "");
  assert.equal(buildInput(form).preset, "eco_beauty");
});

test("진행 카드: 끝난 섹션 수로 %, 대기·만드는 중이면 계속 읽고 끝나면 멈춘다", () => {
  const running = { status: "running" as const, sections: TREND_REPORT_EXAMPLE.sections };
  // 예시는 검색·해시태그(unknown)·콘텐츠·경쟁사가 끝났고 추천은 진행 중 → 4/5
  assert.equal(progressPct(running), 80);
  assert.equal(progressPct({ ...running, status: "partial" }), 100);
  assert.equal(progressKeepPolling("queued"), true);
  assert.equal(progressKeepPolling("running"), true);
  for (const s of ["done", "partial", "failed", "interrupted"] as const) assert.equal(progressKeepPolling(s), false, s);
});

test("버튼 상태: 접수 전 → 넣는 중 → 접수됨(TR-…) / 같은 의뢰 저장본, 접수 뒤에는 다시 못 누른다", () => {
  assert.deepEqual(submitView({ kind: "idle" }), { button: "분석 의뢰 넣기", disabled: false, note: null, openReport: false });
  assert.equal(submitView({ kind: "busy" }).disabled, true);
  assert.deepEqual(submitView({ kind: "done", reportId: "tr_3f2a9c0011223344", reused: false }),
    { button: null, disabled: true, note: "접수됨 · TR-3F2A", openReport: false });
  assert.deepEqual(submitView({ kind: "done", reportId: "tr_3f2a9c0011223344", reused: true }),
    { button: null, disabled: true, note: "같은 의뢰 저장본을 열었어요", openReport: true });
  const missing = submitView({ kind: "error", missing: true, message: "x" });
  assert.equal(missing.disabled, true);
  assert.ok(missing.note?.includes("준비 중"));
  assert.equal(submitView({ kind: "error", missing: false, message: "지금은 받지 못했어요" }).disabled, false);
  assert.equal(reportCode("tr_abcd1234"), "TR-ABCD");
});

test("완료 카드: 완료 초록 · 실패 빨강, 근거 최대 3개, 앱 밖 주소는 쓰지 않는다", () => {
  const ok = doneView(DONE_CARD_EXAMPLE);
  assert.deepEqual(ok, { title: "보고서 완료 · TR-3F2A", tone: "ok", evidence: ["근거 예시 1", "근거 예시 2", "근거 예시 3"] });
  assert.equal(doneView({ ...DONE_CARD_EXAMPLE, status: "partial" }).title, "보고서 일부만 완료 · TR-3F2A");
  assert.equal(doneView(DONE_CARD_FAILED).tone, "bad");
  assert.equal(reportPath(DONE_CARD_EXAMPLE.report_path, DONE_CARD_EXAMPLE.report_id), "/data/trend#request?id=tr_3f2a9c0011223344");
  assert.equal(reportPath(DONE_CARD_FAILED.report_path, DONE_CARD_FAILED.report_id), "/data/trend#request?id=tr_9b8c7d0011223344");
  assert.equal(reportPath("//evil.example/x", "tr_1"), "/data/trend#request?id=tr_1");
});

test("대화 응답의 카드는 서버 모양 그대로 (경쟁사 이름 표 키를 바꾸지 않음), 예전 메시지·모르는 kind는 null", () => {
  const raw = [
    { id: "m1", role: "assistant", text: "카드", at: "", kind: "text",
      card: { ...REQUEST_CARD_EXAMPLE, brief: { ...REQUEST_CARD_EXAMPLE.brief, competitor_names: { toun28_official: "이름" } } } },
    { id: "m2", role: "user", text: "예전", at: "", kind: "text" },
    { id: "m3", role: "assistant", text: "모름", at: "", kind: "text", card: { kind: "something_else" } },
  ];
  // directChat.toThread가 camelize한 뒤 원문 카드를 다시 붙인다 (camelize하면 toun28Official·requestText가 된다)
  const camelized: { taskId: null; messages: ChatMessage[] } = {
    taskId: null,
    messages: raw.map((m) => ({
      id: m.id, text: m.text, at: m.at, kind: "text", role: m.role as "user" | "assistant", card: { requestText: "x" } as unknown as ChatCard,
    })),
  };
  const t = withRawCards(camelized, raw);
  const card = t.messages[0].card;
  assert.ok(card && card.kind === "trend_request");
  if (card?.kind === "trend_request") {
    assert.deepEqual(card.brief.competitor_names, { toun28_official: "이름" });
    assert.equal(card.request_text, REQUEST_CARD_EXAMPLE.request_text);
  }
  assert.equal(t.messages[1].card, null);
  assert.equal(t.messages[2].card, null);
  assert.equal(t.taskId, null); // 나머지 칸은 예전처럼 camelCase
});
