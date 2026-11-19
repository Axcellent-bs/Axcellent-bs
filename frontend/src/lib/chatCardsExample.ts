// 테스트 전용 예시 (chatCards.test.ts). 공통 계약 모양의 데이터분석 대화 카드. 화면에서 부르지 않는다 (데모 데이터 아님).
// 일부러 넣은 경우: 프리셋에서 채운 칸 · 이름 없는 경쟁사 · 경쟁사 4곳 이상 · 키워드 3개 이상 · 실패 보고서 · 근거 4개(3개만 보임).
import type { TrendReportDoneCard, TrendReportProgressCard, TrendRequestCard } from "./planningData.ts";

export const REQUEST_CARD_EXAMPLE: TrendRequestCard = {
  kind: "trend_request",
  request_text: "예시 의뢰문 <b>태그는 글자로</b>",
  brief: {
    product: "예시 제품",
    target_gender: "f",
    target_ages: ["20", "30"],
    format: "인스타 릴스 30초",
    competitors: ["example_a", "example_b", "example_c", "example_d"],
    keywords: ["예시 키워드1", "예시 키워드2", "예시 키워드3"],
    competitor_names: { example_a: "예시 브랜드 A", example_c: "예시 브랜드 C" },
    sources: { product: "request_text", target: "preset", format: "preset", competitors: "preset", keywords: "request_text" },
  },
  preset: "eco_beauty",
  cost_note: "인스타 약 45번·네이버 약 6번·AI 2번",
  brief_reason: null,
};

/** 의뢰문에서 칸을 못 뽑은 카드 (이름 없는 경쟁사 하나, 칸 대부분 비어 있음) */
export const REQUEST_CARD_SPARSE: TrendRequestCard = {
  kind: "trend_request",
  request_text: "예시 짧은 의뢰문",
  brief: {
    product: null, target_gender: null, target_ages: [], format: null, competitors: ["example_x"], keywords: [], competitor_names: {},
    sources: { product: "request_text", target: "request_text", format: "request_text", competitors: "request_text", keywords: "request_text" },
  },
  preset: null,
  cost_note: "인스타 약 10번·네이버 약 0번·AI 1번",
  brief_reason: "의뢰문에서 제품·키워드를 찾지 못했어요",
};

export const PROGRESS_CARD_EXAMPLE: TrendReportProgressCard = { kind: "trend_report_progress", report_id: "tr_3f2a9c0011223344" };

export const DONE_CARD_EXAMPLE: TrendReportDoneCard = {
  kind: "trend_report_done",
  report_id: "tr_3f2a9c0011223344",
  status: "done",
  summary_line: "예시 결론 한 줄",
  top_evidence: ["근거 예시 1", "근거 예시 2", "근거 예시 3", "근거 예시 4"],
  report_path: "/data/trend#request?id=tr_3f2a9c0011223344",
};

export const DONE_CARD_FAILED: TrendReportDoneCard = {
  kind: "trend_report_done",
  report_id: "tr_9b8c7d0011223344",
  status: "failed",
  summary_line: null,
  top_evidence: [],
  report_path: "https://example.com/elsewhere",
};
