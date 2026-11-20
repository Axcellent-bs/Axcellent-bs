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
