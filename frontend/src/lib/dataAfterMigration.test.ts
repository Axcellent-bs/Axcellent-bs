// node --test로 돈다 (npm test). 새 서버 점검 뒤 데이터 화면 글자: 유행 몫 · 월 검색 10 미만 · 접수된 카드 · 보고서 번호 · 판 trigger.
import assert from "node:assert/strict";
import { test } from "node:test";
import { BRIEF_TRIGGER_LABEL, briefTriggerLabel, versionLabel } from "./briefTrigger.ts";
import { doneView, submitState, submitView } from "./chatCards.ts";
import { DONE_CARD_EXAMPLE, REQUEST_CARD_ACCEPTED, REQUEST_CARD_EXAMPLE } from "./chatCardsExample.ts";
import { judgedByShare, shareLine, sharePct } from "./memeView.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { reportLabel, searchMain, searchNotes, volumeText } from "./trendReportView.ts";

test("유행 몫: judged_by share면 '이번 주 몫 · 지난주 몫 (×배수)', count·예전 응답이면 null(영상 수 배수 그대로)", () => {
  const m = { judged_by: "share" as const, share: 0.0123, previous_share: 0.004, share_ratio: 3.075 };
  assert.equal(shareLine(m, "이번 주", "지난주"), "이번 주 몫 1.2% · 지난주 0.4% (×3.1)");
  assert.equal(shareLine({ ...m, share_ratio: null }, "이번 주", "지난주"), "이번 주 몫 1.2% · 지난주 0.4%");
  assert.equal(shareLine({ ...m, previous_share: null }, "어제", "지난 4주 같은 요일"), "어제 몫 1.2% · 지난 4주 같은 요일 모름 (×3.1)");
  assert.equal(shareLine({ ...m, judged_by: "count" }, "이번 주", "지난주"), null);
  assert.equal(shareLine({ share: 0.5, previous_share: 0.1, share_ratio: 5 }, "이번 주", "지난주"), null); // judged_by 없음 = 예전 응답
  assert.equal(judgedByShare({}), false);
  assert.equal(sharePct(0.0004), "0.1% 미만");
  assert.equal(sharePct(0), "0%");
  assert.equal(sharePct(0.25), "25%");
  assert.equal(sharePct(null), null);
});

test("보고서 검색: monthly_volume_note under_10이면 '월 검색 10 미만', 0이고 note 없으면 '월 검색 0' 그대로", () => {
  const rows = TREND_REPORT_EXAMPLE.sections.search.rows;
  const under = rows.find((r) => r.monthly_volume_note === "under_10")!;
  assert.equal(searchMain(under).text, "월 검색 10 미만");
  assert.equal(volumeText({ monthly_volume: 0, monthly_volume_note: null }), "월 검색 0");
  assert.equal(volumeText({ monthly_volume: 0 }), "월 검색 0");
  assert.equal(volumeText({ monthly_volume: 0, monthly_volume_note: "under_10" }), "월 검색 10 미만"); // 0으로 와도 note가 먼저
  assert.equal(volumeText({ monthly_volume: 12300 }), "월 검색 12,300");
  assert.equal(volumeText({ monthly_volume: null }), null);
  const who = { target_gender: "f" as const, target_ages: ["20"] };
  assert.ok(searchNotes({ ...rows[0], monthly_volume: null, monthly_volume_note: "under_10" }, who).includes("월 검색 10 미만"));
  assert.ok(!searchNotes({ ...rows[0], monthly_volume: null, monthly_volume_note: null }, who).some((s) => s.startsWith("월 검색")));
});

test("접수된 카드: 서버 accepted_report_id가 탭 기억보다 먼저 → '접수됨 · TR-B835' + 보고서 열기", () => {
  const accepted = submitState(REQUEST_CARD_ACCEPTED, { kind: "idle" });
  assert.deepEqual(accepted, { kind: "done", reportId: "tr_b8358e0011223344", reused: false, accepted: true });
  assert.deepEqual(submitView(accepted), { button: null, disabled: true, note: "접수됨 · TR-B835", openReport: true });
  // 탭이 다른 결과를 기억해도 서버 값이 이긴다
  assert.equal(submitState(REQUEST_CARD_ACCEPTED, { kind: "error", missing: false, message: "x" }).kind, "done");
  // 서버 값이 없으면 탭 기억, 그것도 없으면 접수 전
  assert.deepEqual(submitState(REQUEST_CARD_EXAMPLE, null), { kind: "idle" });
  const remembered = { kind: "done" as const, reportId: "tr_3f2a9c00", reused: false };
  assert.deepEqual(submitState({ ...REQUEST_CARD_EXAMPLE, accepted_report_id: null }, remembered), remembered);
  assert.equal(submitView(remembered).openReport, false); // 이 탭에서 막 접수한 건 지금처럼 글자만
  assert.equal(submitState({ ...REQUEST_CARD_EXAMPLE, accepted_report_id: "  " }, null).kind, "idle");
});

test("보고서 번호: 'TR-' + 'tr_' 뒤 앞 4글자 대문자 (화면·대화 카드 같은 함수)", () => {
  assert.equal(reportLabel("tr_b8358e9a01"), "TR-B835");
  assert.equal(reportLabel("tr_3f2a9c0011223344"), "TR-3F2A");
  assert.equal(reportLabel("abcd1234"), "TR-ABCD"); // tr_ 없으면 앞 4글자
  assert.equal(doneView(DONE_CARD_EXAMPLE).title, "보고서 완료 · TR-3F2A");
  assert.ok(submitView({ kind: "done", reportId: "tr_b8358e", reused: false }).note?.endsWith("TR-B835"));
});

test("인사이트 판 trigger: auto_retry는 '자동(다시)'", () => {
  assert.equal(BRIEF_TRIGGER_LABEL.auto_retry, "자동(다시)");
  assert.equal(briefTriggerLabel("auto_retry"), "자동(다시)");
  assert.equal(briefTriggerLabel("auto"), "자동");
  assert.equal(briefTriggerLabel(null), null);
  assert.equal(versionLabel({ label: "00:27 다시 추천", trigger: "auto_retry" }), "00:27 자동(다시)");
  assert.equal(versionLabel({ label: "00:27 자동", trigger: "auto" }), "00:27 자동"); // 그 밖은 서버 이름 그대로
  assert.equal(versionLabel({ label: "11:16 다시 추천", trigger: "manual" }), "11:16 다시 추천");
  assert.equal(versionLabel({ label: "첫 판", trigger: "auto_retry" }), "첫 판 · 자동(다시)");
});
