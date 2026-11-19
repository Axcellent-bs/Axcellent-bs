// Instagram 자동 게시 스위치(마케팅 `get_publish_control`·`set_publish_control`, dashboard-api 중계).
// 자동 게시만 끄고 초안·미디어·자동 넘김은 그대로 돈다(수동 게시는 후속 PR). 순수 계산은 `npm test`가 그대로 돈다.
// 값은 서버가 준 것만 쓴다. 끄기는 바로 적용(A 등급), 켜기는 사람 승인(C 등급, 보류되면 승인 뒤 다시 누른다).
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";

export interface PublishControlLogItem { paused: boolean; reason: string; updated_by: string; updated_at: string }

export interface PublishControlState {
  channel: string;
  paused: boolean;
  reason: string;
  updated_by: string | null;
  updated_at: string | null;
  /** db = 사람이 바꾼 기록이 있다, default = 행이 없다(기본값) */
  source: "db" | "default";
  history: PublishControlLogItem[];
}

export interface PublishControlAnswer {
  status: string;
  outcome: string | null;
  output: (Partial<PublishControlState> & { review_id?: string; reason?: string }) | null;
}

export interface PublishControlLine { tone: "ok" | "warn" | "bad"; title: string; detail: string }

/** 현재 상태 한 줄. 서버가 상태를 못 줬으면 읽을 수 없다고 적는다(게시 가능이라고 짐작하지 않는다) */
export function publishControlLine(state: PublishControlState | null | undefined, shown: (iso: string) => string = (s) => s): PublishControlLine {
  if (!state) return { tone: "bad", title: "자동 게시 상태를 읽을 수 없음", detail: "서버가 자동 게시 스위치를 주지 않았습니다. 이 경우 서버는 안전하게 자동 게시를 막습니다." };
  const who = state.updated_by && state.updated_at ? `${state.updated_by} · ${shown(state.updated_at)}` : "바뀐 기록 없음(기본값)";
  if (state.paused) {
    return { tone: "warn", title: "자동 게시가 꺼져 있습니다", detail: `${state.reason ? `사유: ${state.reason}. ` : ""}수동 게시로 전환 예정입니다. 초안과 미디어 생성은 계속 쓸 수 있습니다. 바꾼 사람·시각: ${who}` };
  }
  return { tone: "ok", title: "자동 게시 켜짐", detail: `게시는 그대로 사람 승인(C 등급)을 거칩니다. 바꾼 사람·시각: ${who}` };
}

export interface PublishControlResult { kind: "updated" | "held" | "unchanged" | "failed"; text: string }

const FAIL_KO: Record<string, string> = {
  publish_control_error: "서버의 자동 게시 스위치를 읽거나 저장할 수 없어 바꾸지 못했습니다. 자동 게시는 막힌 상태로 둡니다.",
  invalid_input: "입력이 맞지 않아 바꾸지 못했습니다.",
};

/** 켜고 끄기 응답을 화면 문구로. 재개가 보류(held)되면 승인 방법을 알린다 */
export function publishControlResultKo(answer: PublishControlAnswer): PublishControlResult {
  if (answer.outcome === "updated") {
    return { kind: "updated", text: answer.output?.paused ? "자동 게시를 껐습니다. 바로 적용됩니다." : "자동 게시를 켰습니다." };
  }
  if (answer.outcome === "unchanged") return { kind: "unchanged", text: "이미 같은 상태입니다." };
  if (answer.status === "input-required" || answer.outcome === "held") {
    return { kind: "held", text: "자동 게시를 다시 켜려면 사람 승인이 필요합니다. 승인 화면에서 승인한 뒤 '자동 게시 켜기'를 한 번 더 누르면 적용됩니다." };
  }
  return { kind: "failed", text: (answer.outcome && FAIL_KO[answer.outcome]) || `바꾸지 못했습니다${answer.outcome ? ` (${answer.outcome})` : ""}. 잠시 뒤 다시 시도해 주세요.` };
}

/** 연결 오류(HTTP 상태)를 화면 문구로. 서버가 보낸 문장은 쓰지 않는다 */
export function publishControlHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "자동 게시 상태를 바꿀 권한이 없습니다. 사람 토큰 설정을 확인해 주세요.";
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "입력을 서버가 받아들이지 않았습니다. 사유는 200자까지입니다.";
  return "자동 게시 상태 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}
