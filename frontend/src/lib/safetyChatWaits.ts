// 안전 처리 로그 › 사람 결정 대기 › 대화방 의뢰 · 사람 확인 (11/26). node --test (safetyChatWaits.test.ts).
// 총괄이 접수한 의뢰(GET /api/requests)에서 사람을 기다리는 단계(`waiting_human`)를 한 줄씩 뽑는다.
// - 기획 확인(human_check)·회사 캐릭터 답(character_wait)·승인 필요(needs_approval) 등은 총괄 안에만 남아
//   안전 관문 C 보류 목록에 안 잡힌다. 그래서 여기서 따로 모은다
// - 영상 승인 대기(held + review_id)는 C 보류 목록에 이미 있으니 다시 세지 않는다
// - 삭제(숨김)·멈춘 의뢰, 끝난 의뢰는 뺀다. 결정은 대화방 의뢰 카드에서 한다(여기는 링크만)
import type { RequestStatus, WorkRequestRow, WorkRequestTask } from "./workRequests.ts";

export interface ChatWait {
  key: string;
  requestId: string;
  what: string;
  why: string;
  since: string;
  href: string;
}

const CLOSED: ReadonlySet<RequestStatus> = new Set(["done", "failed", "rejected", "needs_human"]);

export const CHAT_WAIT_WHY: Record<string, string> = {
  human_check: "사람 확인을 눌러야 다음 단계로 가요",
  character_wait: "회사 캐릭터를 골라야 시작해요",
  needs_approval: "상태를 바꾸는 일이라 사람 승인을 기다려요",
};
const WHY_DEFAULT = "사람이 정해야 넘어가요";

export const CHAT_ROOM_HREF = "/agents/chat?rooms=all";

type Row = Pick<WorkRequestRow, "request_id" | "status" | "task" | "plan" | "steps" | "updated_at" | "hidden_at" | "stopped_at">;

export function chatWaits(rows: readonly Row[], tasks: readonly WorkRequestTask[]): ChatWait[] {
  const out: ChatWait[] = [];
  for (const row of rows) {
    if (CLOSED.has(row.status) || row.hidden_at || row.stopped_at) continue;
    const title = tasks.find((t) => t.name === row.task)?.label ?? "의뢰";
    for (const p of row.plan) {
      const step = row.steps[p.step_id];
      if (!step || step.status !== "waiting_human") continue;
      out.push({
        key: `req:${row.request_id}:${p.step_id}`,
        requestId: row.request_id,
        what: `${title} · ${p.label}`,
        why: (step.reason_code && CHAT_WAIT_WHY[step.reason_code]) || WHY_DEFAULT,
        since: step.started_at ?? row.updated_at,
        href: CHAT_ROOM_HREF,
      });
    }
  }
  return out.sort((a, b) => a.since.localeCompare(b.since));
}
