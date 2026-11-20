import type { CommLine } from "./types";
import { OPEN_REQUEST_STATES, requestContext, type RequestStatus } from "./workRequests.ts";

// AI 대화방에서 공통 로그 소통 줄 중 "일과 상관없는 조회"를 가른다 (lib/agentChat.ts commItems가 쓴다).
// 값으로 가져오는 것은 import가 없는 lib/workRequests.ts뿐이다: `node --test`로 바로 시험한다 (commFilter.test.ts).
// 원본 공통 로그는 그대로이고, 화면에서만 뺀다.

/** 실패·보류·거절·사람 확인은 화면 줄·조회 줄이어도 남긴다 */
export const SCREEN_KEEP_OUTCOMES: ReadonlySet<string> = new Set(["failed", "rejected", "held", "needs_human"]);

// 사건(ctx_inc_)·변경 요청(ctx_req_)·고객 의뢰(ctx_wr_) 안의 조회는 일의 흐름이라 남긴다.
// 그 밖의 조회는 화면 탭을 열 때마다 쌓인다(재무 → 인사 팀 인원, 문의 → 기억 카드 검색 등).
// 11/20 14:36 의뢰 wr_732efb95e9: 총괄 → 데이터 trend_report_create(read)가 ctx_wr_로 남는데 여기서 걸러져 말풍선이 없었다
export const WORK_CONTEXT = /^ctx_(inc|req|wr)_/;

// 화면이 쓰던 고정 context. 이름은 ctx_req_로 시작하지만 변경 요청이 아니라 데이터 화면 조회다.
// 11/20 12:08: 트렌드 화면이 이 context로 data trend_insight_brief를 부르자 data가 같은 context로
// 고객지원 book_issue_stats를 물었고, 그 줄이 "요청 안의 일"로 남아 전체 대화방의 의뢰 아래에 끼었다.
// 지금은 화면이 ctx_ui_dashboard를 쓴다(lib/data.ts). 이미 쌓인 예전 기록을 빼려고 남겨 둔다
export const LEGACY_SCREEN_CONTEXTS: ReadonlySet<string> = new Set(["ctx_req_dashboard"]);

// 사건이 닫힌 뒤의 조회는 일의 흐름이 아니라 화면 확인이다: 재무 화면이 그릴 때마다 지난 사건 8개의 영향 분석을
// 다시 묻던 "×16" 줄 (2026-11-12, 251). 총괄이 직접 묻거나 받은 줄은 닫힌 뒤 단계(복구 확인·정상화 공지 등)일 수 있어 남긴다
const CENTRAL_AGENT = "orchestrator";

/**
 * 일과 상관없는 조회 줄인가 (등급 read, 성공·진행 중인 줄만).
 * - 사건·요청(ctx_inc_·ctx_req_) 밖의 조회 (화면이 쓰던 고정 context `LEGACY_SCREEN_CONTEXTS`도 밖)
 * - 사건이 닫힌 뒤(`closedAt`에 있는 그 사건의 닫힌 시각보다 늦게) 생긴 조회. 총괄이 오간 줄은 남긴다
 * 쓰기(read가 아닌 등급) 줄과 실패·보류·거절·사람 확인 줄은 언제나 남긴다
 */
export function isBackgroundRead(line: CommLine, closedAt?: ReadonlyMap<string, string>): boolean {
  if (line.grade !== "read" || SCREEN_KEEP_OUTCOMES.has(line.outcome)) return false;
  const ctx = line.context_id ?? "";
  if (!WORK_CONTEXT.test(ctx) || LEGACY_SCREEN_CONTEXTS.has(ctx)) return true;
  if (line.from_agent === CENTRAL_AGENT || line.to_agent === CENTRAL_AGENT) return false;
  const closed = closedAt?.get(ctx);
  if (!closed) return false; // 진행 중인 사건의 조회는 일의 흐름
  const at = Date.parse(line.occurred_at);
  const end = Date.parse(closed);
  return Number.isFinite(at) && Number.isFinite(end) && at > end;
}

/** 사건 번호 → 닫힌 시각 (총괄 사건의 closed_at, 닫힌 사건만) */
export const closedTimes = (incidents: { context_id: string; closed_at: string | null }[]): ReadonlyMap<string, string> =>
  new Map(incidents.filter((i) => i.closed_at).map((i) => [i.context_id, i.closed_at as string]));

/** 의뢰 context(ctx_wr_…) → 닫힌 시각: 끝난 의뢰(열린 상태가 아닌 것)의 updated_at. 사건 closedTimes와 같은 규칙으로 끝난 뒤 조회를 거른다 */
export const requestClosedTimes = (
  requests: { request_id: string; context_id?: string | null; status: RequestStatus; updated_at: string }[],
): ReadonlyMap<string, string> =>
  new Map(requests.filter((r) => !OPEN_REQUEST_STATES.has(r.status) && r.updated_at).map((r) => [requestContext(r), r.updated_at]));

/** 사건·의뢰 닫힌 시각을 하나로 */
export const mergeClosed = (...maps: ReadonlyMap<string, string>[]): ReadonlyMap<string, string> => {
  const out = new Map<string, string>();
  for (const m of maps) for (const [k, v] of m) out.set(k, v);
  return out;
};

// ---------------------------------------------------------------- 대화 줄 / 작업 기록 줄 나누기
// 11/20 16:46~16:49 전체 대화방에 말풍선 9개가 떴는데 사람이 읽을 대화는 하나도 없었다:
// 총괄↔마케팅 generate_media 실패 ×3(의뢰 wr_ada8f37811 안) · 기획→개발 start_dev ×5(REQ-21·30·34·45·DEV-1 다시 보냄) ·
// 화면→마케팅 creative_video_request 실패 · 기획→회사 기억 card_write · 안전 처리 통과.
// 여기서 "log"로 가른 줄은 대화 칸에서 접히고("작업 기록 N줄 접힘"), 펼치면 제자리에 회색 작은 말풍선으로 보인다.
// 원본 공통 로그와 안전 처리 로그 방(SAFETY_ROOM)은 그대로다. AI 오피스 그림(걷기·불꽃)은 모든 줄로 계산한다.

export type Lane = "chat" | "log";

const SCREEN_AGENT = "dashboard-api";
const MEMORY_WRITE = /(^|\.)card_write$/;
/** 사람이 꼭 봐야 하는 줄: 보류·사람 확인 필요는 어느 규칙에 걸려도 대화로 남긴다 */
const MUST_SEE: ReadonlySet<string> = new Set(["held", "needs_human"]);
/** 다시 시도한 실패: 같은 키가 이 시간 안에 뒤에서 completed면 실패도 접는다 */
export const RETRY_WINDOW_MS = 5 * 60_000;

/** 같은 일을 가르는 키: 보낸 곳 · 받는 곳 · skill · context_id */
export const commKey = (l: Pick<CommLine, "from_agent" | "to_agent" | "skill" | "context_id">) =>
  [l.from_agent, l.to_agent ?? "", l.skill ?? "", l.context_id ?? ""].join("|");

export interface LaneContext {
  /** 이 방에 의뢰 진행 카드가 실제로 보이는 의뢰의 context (ctx_wr_…). 카드가 없는 방·토큰이 없으면 비어 있다 */
  cardContexts: ReadonlySet<string>;
  /** 이 줄보다 앞에서 이미 completed였던 키 (commKey) */
  completedBefore: ReadonlySet<string>;
  /** 이 줄 뒤 RETRY_WINDOW_MS 안에 같은 키가 completed가 됐나 (다시 시도해 끝난 실패) */
  retriedOk: boolean;
  /** 안전 처리 로그 방이면 참 (⑤를 접지 않는다) */
  safetyRoom?: boolean;
}

/**
 * 공통 로그 한 줄이 대화(chat)인가 작업 기록(log)인가.
 * - 사건(ctx_inc_) 흐름은 언제나 chat
 * - ① 의뢰 진행 카드가 보이는 방의 그 의뢰(ctx_wr_) 안 부서 호출 → log (카드가 단계·실패를 보여 준다)
 * - ② 다시 보낸 줄: 같은 키가 앞에서 이미 completed → log (그 대답 줄도 같은 키라 같이)
 * - ③ 다시 시도한 실패: rejected인데 5분 안에 같은 키가 뒤에서 completed → log
 * - ④ 화면 호출: dashboard-api가 보내거나 받았고 context가 사건·요청·의뢰 밖 → log (실패도. 화면이 자기 오류를 보여 준다)
 * - ⑤ 회사 기억 쓰기(card_write)·안전 처리(safety) 통과 → log. 안전 처리 로그 방에서는 접지 않는다
 * - 보류(held)·사람 확인 필요(needs_human)는 어느 규칙에 걸려도 chat. 위에 안 걸리는 실패도 chat
 */
export function chatLane(line: CommLine, ctx: LaneContext): Lane {
  const c = line.context_id ?? "";
  if (c.startsWith("ctx_inc_")) return "chat";
  if (MUST_SEE.has(line.outcome)) return "chat";
  if (c && ctx.cardContexts.has(c)) return "log"; // ①
  if (ctx.completedBefore.has(commKey(line))) return "log"; // ②
  if (line.outcome === "rejected" && ctx.retriedOk) return "log"; // ③
  const screen = line.from_agent === SCREEN_AGENT || line.to_agent === SCREEN_AGENT;
  if (screen && !WORK_CONTEXT.test(c)) return "log"; // ④
  if (!ctx.safetyRoom && line.outcome !== "rejected") {
    if (line.to_agent === "memory" && MEMORY_WRITE.test(line.skill ?? "")) return "log"; // ⑤ 회사 기억 쓰기
    if (line.from_agent === "safety" || line.to_agent === "safety") return "log"; // ⑤ 안전 처리 통과
  }
  return "chat";
}

const ms = (iso: string) => { const t = Date.parse(iso); return Number.isFinite(t) ? t : 0; };

/** 여러 줄을 시각순으로 보며 앞에서 끝난 키·뒤에서 다시 끝난 실패를 계산해 줄마다 lane (log_id → lane) */
export function chatLanes(lines: readonly CommLine[], opts: { cardContexts?: ReadonlySet<string>; safetyRoom?: boolean } = {}): Map<string, Lane> {
  const sorted = [...lines].sort((a, b) => ms(a.occurred_at) - ms(b.occurred_at));
  const doneAt = new Map<string, number[]>(); // 키 → completed 시각들
  for (const l of sorted) if (l.outcome === "completed") (doneAt.get(commKey(l)) ?? doneAt.set(commKey(l), []).get(commKey(l))!).push(ms(l.occurred_at));
  const completedBefore = new Set<string>();
  const out = new Map<string, Lane>();
  const cardContexts = opts.cardContexts ?? new Set<string>();
  for (const l of sorted) {
    const key = commKey(l);
    const t = ms(l.occurred_at);
    const retriedOk = l.outcome === "rejected" && (doneAt.get(key) ?? []).some((d) => d > t && d - t <= RETRY_WINDOW_MS);
    out.set(l.log_id, chatLane(l, { cardContexts, completedBefore, retriedOk, safetyRoom: opts.safetyRoom }));
    if (l.outcome === "completed") completedBefore.add(key);
  }
  return out;
}
