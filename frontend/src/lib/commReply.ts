// 공통 로그 진행 중(in_progress) 줄 중 "받았어요" 답장을 가른다 (대화방 작업 기록·AI 오피스 말풍선이 같은 판단을 쓴다)
// 같은 context_id·같은 skill에서 앞에 A → B 진행 중 줄이 있고, 이 줄이 B → A 진행 중이면
// 부서가 긴 작업으로 받았다고 답한 줄이다. 짝을 못 찾으면 지금처럼 부탁이다.
import type { CommLine } from "./types";

type ReplyLine = Pick<CommLine, "log_id" | "occurred_at" | "from_agent" | "to_agent" | "skill" | "outcome" | "context_id">;

const at = (iso: string) => {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** 답장(받았어요)인 줄의 log_id. 안전 관문(from safety) 줄은 답장으로 보지 않는다 */
export function replyLogIds(lines: readonly ReplyLine[]): Set<string> {
  const ordered = lines
    .map((line, i) => ({ line, i }))
    .filter(({ line }) => line.outcome === "in_progress" && line.context_id && line.skill && line.to_agent && line.from_agent !== "safety")
    .sort((a, b) => at(a.line.occurred_at) - at(b.line.occurred_at) || a.i - b.i);
  const asked = new Set<string>();
  const out = new Set<string>();
  const key = (from: string, to: string, l: ReplyLine) => JSON.stringify([l.context_id, l.skill, from, to]);
  for (const { line } of ordered) {
    const to = line.to_agent as string;
    if (asked.has(key(to, line.from_agent, line))) out.add(line.log_id);
    else asked.add(key(line.from_agent, to, line));
  }
  return out;
}
