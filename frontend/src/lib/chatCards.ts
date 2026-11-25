// 데이터분석 1:1 대화 카드(components/office/ChatCards.tsx)의 순수 계산. node --test로 시험한다 (chatCards.test.ts).
// 카드 모양은 공통 계약(lib/planningData.ts ChatCard). 서버 글(의뢰문·결론·근거)은 그대로 글자로만 쓴다.
import type { ReportStatus, TrendReport, TrendReportInput } from "./data.ts";
import type { ChatCard, ChatMessage, TrendRequestCard, TrendReportDoneCard } from "./planningData.ts";
import { THEME_MODE_LABEL, countedSections, formFromBrief, keepPolling, reportHash, reportLabel, targetText, type RequestForm } from "./trendReportView.ts";

// ---- 대화 응답 → 카드 (서버 모양 그대로)

const CARD_KINDS = new Set(["trend_request", "trend_report_progress", "trend_report_done"]);

/** 서버 메시지 하나의 card. 모르는 kind·모양이면 null (그리지 않는다) */
export function rawCard(message: unknown): ChatCard | null {
  const c = message && typeof message === "object" ? (message as { card?: unknown }).card : null;
  return c && typeof c === "object" && CARD_KINDS.has(String((c as { kind?: unknown }).kind)) ? (c as ChatCard) : null;
}

/** 대화방(단톡방) 말풍선에 붙일 데이터분석 카드 (11/23). 1:1(DirectChat)과 같은 ChatCardView를 다시 쓴다.
 *  messageId는 서버 메시지 id 그대로(1:1과 같은 값이라 [의뢰 넣기] 상태가 두 화면에서 같다), threadId는 그 메시지의 대화 id.
 *  분석 카드(trend_request·trend_report_progress·trend_report_done)만. 이미지 시트 카드(creative)는 따로 그린다 */
export interface RoomChatCard { card: ChatCard; messageId: string; threadId: string | null }
export function roomChatCard(message: { id: string; card?: unknown } | null | undefined, threadId: string | null | undefined): RoomChatCard | null {
  const card = message ? rawCard(message) : null;
  return card && message ? { card, messageId: message.id, threadId: threadId || null } : null;
}

/** camelize한 대화에 서버 원문 메시지의 card를 그대로 다시 붙인다 (competitor_names 키 = 인스타 아이디를 지키려고) */
export function withRawCards<T extends { messages: ChatMessage[] }>(thread: T, rawMessages: unknown[] | null | undefined): T {
  const raw = Array.isArray(rawMessages) ? rawMessages : [];
  return { ...thread, messages: thread.messages.map((m, i) => ({ ...m, card: rawCard(raw[i]) })) };
}

// ---- 의뢰 카드 표시

export interface CardRow {
  key: "product" | "theme" | "target" | "format" | "competitors" | "keywords";
  label: string;
  value: string;
  /** 이 칸을 프리셋에서 채웠으면 "프리셋" 꼬리표 */
  preset: boolean;
}

export const COMPETITORS_SHOWN = 3;
export const KEYWORDS_SHOWN = 2;

/** 경쟁사: 이름이 있으면 이름, 없으면 @아이디. 3개 넘으면 "외 N곳" */
export function competitorsText(ids: string[], names: Record<string, string> | null | undefined): string {
  if (ids.length === 0) return "없음";
  const shown = ids.slice(0, COMPETITORS_SHOWN).map((id) => names?.[id]?.trim() || `@${id}`);
  const rest = ids.length - COMPETITORS_SHOWN;
  return rest > 0 ? `${shown.join(", ")} 외 ${rest}곳` : shown.join(", ");
}

/** 키워드: 2개 + "외 N개" */
export function keywordsText(keywords: string[]): string {
  if (keywords.length === 0) return "없음";
  const rest = keywords.length - KEYWORDS_SHOWN;
  const shown = keywords.slice(0, KEYWORDS_SHOWN).join(", ");
  return rest > 0 ? `${shown} 외 ${rest}개` : shown;
}

export function requestCardRows(card: TrendRequestCard): CardRow[] {
  const b = card.brief;
  const src = b.sources;
  const rows: CardRow[] = [
    { key: "product", label: "제품", value: b.product ?? "정하지 않음", preset: src.product === "preset" },
    { key: "target", label: "타깃", value: targetText(b.target_gender, b.target_ages), preset: src.target === "preset" },
    { key: "format", label: "결과물", value: b.format ?? "정하지 않음", preset: src.format === "preset" },
    { key: "competitors", label: "경쟁사", value: competitorsText(b.competitors, b.competitor_names), preset: src.competitors === "preset" },
    { key: "keywords", label: "키워드", value: keywordsText(b.keywords), preset: src.keywords === "preset" },
  ];
  const theme = b.theme?.trim();
  if (theme) {
    // "아이돌 · 찾아서 순위" (주제가 있을 때만, 제품 다음 줄)
    rows.splice(1, 0, { key: "theme", label: "주제", value: b.theme_mode ? `${theme} · ${THEME_MODE_LABEL[b.theme_mode]}` : theme, preset: false });
  }
  return rows;
}

/** "분석 의뢰 넣기"가 보내는 값: 의뢰문 + 카드 칸 그대로 + caller human + 대화 id (칸이 오면 서버는 의뢰문을 다시 읽지 않는다) */
export function cardInput(card: TrendRequestCard, threadId: string | null): TrendReportInput {
  const b = card.brief;
  return {
    request_text: card.request_text,
    product: b.product,
    target_gender: b.target_gender,
    target_ages: [...b.target_ages],
    format: b.format,
    competitors: [...b.competitors],
    keywords: [...b.keywords],
    caller: "human",
    chat_thread_id: threadId,
    // 칸 값으로 접수하면 서버가 의뢰문을 다시 읽지 않으니 주제도 같이 넘긴다 (#1540). 주제가 없으면 칸을 넣지 않는다(서버 기본 null)
    ...(b.theme?.trim() ? { theme: b.theme.trim(), theme_mode: b.theme_mode ?? "use" } : {}),
    // 11/26: 주제 확장어도 같이 (예전 카드는 칸이 없어 서버가 의뢰문을 한 번 읽는다)
    ...(b.theme?.trim() && b.related_terms ? { related_terms: [...b.related_terms].slice(0, 6) } : {}),
  };
}

/** "분석 의뢰 탭에서 고치기": 카드 값을 "직접 정하기" 폼으로 (보고서 손보기의 formFromBrief 재사용). 의뢰문은 비운다 */
export function formFromCard(card: TrendRequestCard): RequestForm {
  const b = card.brief;
  return formFromBrief({
    product: b.product,
    target_gender: b.target_gender,
    target_ages: b.target_ages,
    format: b.format,
    competitors: b.competitors,
    keywords: b.keywords,
    preset: card.preset,
    sources: { product: b.sources.product, target: b.sources.target, format: b.sources.format, competitors: b.sources.competitors, keywords: b.sources.keywords },
    competitor_names: b.competitor_names,
    theme: b.theme ?? null,
    theme_mode: b.theme_mode ?? null,
  });
}

// ---- 의뢰 버튼 상태

export type SubmitState =
  | { kind: "idle" }
  | { kind: "busy" }
  /** accepted = 서버 카드의 accepted_report_id로 안 접수 (새로고침해도 남는다) */
  | { kind: "done"; reportId: string; reused: boolean; accepted?: boolean }
  /** missing = 서버에 skill이 아직 없음(준비 중). 다시 누를 수 없다 */
  | { kind: "error"; missing: boolean; message: string };

export interface SubmitView {
  /** 버튼 글자. null이면 버튼 대신 note만 */
  button: string | null;
  disabled: boolean;
  note: string | null;
  /** 접수된 뒤 "보고서 열기" 링크 (reused·서버가 접수됐다고 준 카드) */
  openReport: boolean;
}

/** 카드의 버튼 상태: 서버가 accepted_report_id를 주면 그것이 먼저(이미 접수), 없으면 이 탭이 기억한 상태, 그것도 없으면 접수 전 */
export function submitState(card: Pick<TrendRequestCard, "accepted_report_id">, remembered: SubmitState | null | undefined): SubmitState {
  const accepted = card.accepted_report_id?.trim();
  if (accepted) return { kind: "done", reportId: accepted, reused: false, accepted: true };
  return remembered ?? { kind: "idle" };
}

/** 접수 전 → 넣는 중 → 접수됨(TR-…) / 같은 의뢰 저장본. 한 번 접수되면 다시 누를 수 없다 */
export function submitView(s: SubmitState): SubmitView {
  switch (s.kind) {
    case "idle": return { button: "분석 의뢰 넣기", disabled: false, note: null, openReport: false };
    case "busy": return { button: "의뢰 넣는 중…", disabled: true, note: null, openReport: false };
    case "done": return s.reused
      ? { button: null, disabled: true, note: "같은 의뢰 저장본을 열었어요", openReport: true }
      : { button: null, disabled: true, note: `접수됨 · ${reportLabel(s.reportId)}`, openReport: s.accepted === true };
    case "error": return s.missing
      ? { button: "분석 의뢰 넣기", disabled: true, note: "분석 의뢰 기능을 데이터 AI에 붙이는 중이에요 (준비 중)", openReport: false }
      : { button: "분석 의뢰 넣기", disabled: false, note: s.message, openReport: false };
  }
}

// ---- 진행 카드

/** 주제 후보 섹션이 없는 보고서의 칸 수 (주제 후보가 보이는 find 의뢰면 6) */
export const SECTION_COUNT = 5;
const FINISHED = new Set(["known", "unknown", "failed", "empty"]);

/** 진행 칸 수: 보고서 화면과 같은 규칙(주제 후보는 보일 때만 센다) */
export function sectionCount(r: Pick<TrendReport, "sections"> & { brief?: TrendReport["brief"] }): number {
  return countedSections(r).length;
}

/** 진행 %: 끝난 섹션 수 ÷ 칸 수 (서버 섹션 상태로 센다). 보고서가 끝났으면 100 */
export function progressPct(r: Pick<TrendReport, "status" | "sections"> & { brief?: TrendReport["brief"] }): number {
  if (!keepPolling(r.status)) return 100;
  const names = countedSections(r);
  const done = names.filter((n) => FINISHED.has(r.sections[n]?.status ?? "waiting")).length;
  return Math.round((done / names.length) * 100);
}

/** 진행 카드가 다시 읽을지: 대기·만드는 중이면 5초마다, 끝나면 멈춘다 */
export const progressKeepPolling = (status: ReportStatus) => keepPolling(status);

// ---- 완료 카드

export interface DoneView { title: string; tone: "ok" | "warn" | "bad"; evidence: string[] }

export function doneView(card: TrendReportDoneCard): DoneView {
  const code = reportLabel(card.report_id);
  const evidence = card.top_evidence.slice(0, 3);
  if (card.status === "partial") return { title: `보고서 일부만 완료 · ${code}`, tone: "warn", evidence };
  if (card.status === "failed") return { title: `보고서 실패 · ${code}`, tone: "bad", evidence };
  if (card.status === "interrupted") return { title: `보고서 중단 · ${code}`, tone: "bad", evidence };
  return { title: `보고서 완료 · ${code}`, tone: "ok", evidence };
}

/** 보고서 주소: 서버 report_path가 앱 안 주소(/로 시작, //는 아님)일 때만, 아니면 id로 만든다 */
export function reportPath(path: string | null | undefined, id: string): string {
  return path && path.startsWith("/") && !path.startsWith("//") ? path : `/data/trend${reportHash(id)}`;
}

// ---- 분석 의뢰 탭으로 넘기는 값 (react-router location.state)

export interface TrendNavState {
  /** "직접 정하기"에 미리 채울 폼 */
  trendRequestFill?: RequestForm;
  /** 보고서를 열자마자 인쇄(PDF로 저장) */
  trendReportPrint?: boolean;
}
