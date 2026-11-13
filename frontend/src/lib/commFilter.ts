import type { CommLine } from "./types";

// AI 대화방에서 공통 로그 소통 줄 중 "일과 상관없는 조회"를 가른다 (lib/agentChat.ts commItems가 쓴다).
// 이 파일은 다른 모듈을 값으로 가져오지 않는다: `node --test`로 바로 시험한다 (commFilter.test.ts).
// 원본 공통 로그는 그대로이고, 화면에서만 뺀다.

/** 실패·보류·거절·사람 확인은 화면 줄·조회 줄이어도 남긴다 */
export const SCREEN_KEEP_OUTCOMES: ReadonlySet<string> = new Set(["failed", "rejected", "held", "needs_human"]);

// 사건(ctx_inc_)·변경 요청(ctx_req_) 안의 조회는 일의 흐름이라 남긴다.
// 그 밖의 조회는 화면 탭을 열 때마다 쌓인다(재무 → 인사 팀 인원, 문의 → 기억 카드 검색 등)
export const WORK_CONTEXT = /^ctx_(inc|req)_/;

// 사건이 닫힌 뒤의 조회는 일의 흐름이 아니라 화면 확인이다: 재무 화면이 그릴 때마다 지난 사건 8개의 영향 분석을
// 다시 묻던 "×16" 줄 (2026-11-12, 251). 총괄이 직접 묻거나 받은 줄은 닫힌 뒤 단계(복구 확인·정상화 공지 등)일 수 있어 남긴다
const CENTRAL_AGENT = "orchestrator";

/**
 * 일과 상관없는 조회 줄인가 (등급 read, 성공·진행 중인 줄만).
 * - 사건·요청(ctx_inc_·ctx_req_) 밖의 조회
 * - 사건이 닫힌 뒤(`closedAt`에 있는 그 사건의 닫힌 시각보다 늦게) 생긴 조회. 총괄이 오간 줄은 남긴다
 * 쓰기(read가 아닌 등급) 줄과 실패·보류·거절·사람 확인 줄은 언제나 남긴다
 */
export function isBackgroundRead(line: CommLine, closedAt?: ReadonlyMap<string, string>): boolean {
  if (line.grade !== "read" || SCREEN_KEEP_OUTCOMES.has(line.outcome)) return false;
  const ctx = line.context_id ?? "";
  if (!WORK_CONTEXT.test(ctx)) return true;
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
