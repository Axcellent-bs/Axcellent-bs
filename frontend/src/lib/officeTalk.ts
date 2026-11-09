import { ALL_ROOM, type ChatItem } from "@/lib/agentChat";

// 사람이 부서 AI에게 직접 건 대화(useOfficeTalk의 items)를 부서 기록(buildTimeline)에 섞는다.
// AI 오피스 단톡방과 AI 대화방 전체 대화방이 같이 쓴다.

/**
 * 실시간은 사람 대화를 시각순으로 끼워 넣는다(부서 기록의 순서는 다시 정렬하지 않는다).
 * 리플레이(예시 사건)는 실제 대화와 섞지 않고 구분선 아래에 붙인다.
 */
export function mergeTalk(items: ChatItem[], human: ChatItem[], replay = false): ChatItem[] {
  if (human.length === 0) return items;
  if (replay) {
    return [...items, { kind: "separator", id: "direct:replay-sep", at: human[0].at, room: ALL_ROOM, text: "여기부터 실제 대화 (리플레이와 별개)", variant: "topic" }, ...human];
  }
  const out: ChatItem[] = [];
  let h = 0;
  for (const t of items) {
    if (t.at) {
      const at = Date.parse(t.at);
      while (h < human.length && Date.parse(human[h].at) < at) out.push(human[h++]);
    }
    out.push(t);
  }
  while (h < human.length) out.push(human[h++]);
  return out;
}
