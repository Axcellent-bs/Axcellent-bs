import type { ConversationListItem } from "@/lib/types";

// 고객지원 업무 보드: 왼쪽 "처리할 일" → 오른쪽 "처리된 일".
// 두 모드 모두 실제 상담 목록(/conversations)만 쓴다. 데이터를 지어내지 않는다.
//  - 실제: 지금 상태 그대로 (진행 중 → 왼쪽, 해결·이관·답변 완료 → 오른쪽)
//  - 재생: 같은 목록을 처음부터 처리하는 모습으로 다시 보여 준다 (영상·설명용)

export type Stage = "new" | "drafting" | "review" | "sent" | "resolved" | "escalated";
export type Lang = "ko" | "en" | "ja" | "zh" | "other";

export interface BoardCard {
  id: string;
  who: string;
  subject: string;
  lang: Lang;
  urgency: "low" | "medium" | "high" | null;
  stage: Stage;
  /** 이 카드를 지금 만지는 일꾼 (재생 모드에서만) */
  worker?: "ai" | "human";
}

export const TODO_STAGES: Stage[] = ["new", "drafting", "review"];
export const isDone = (s: Stage) => !TODO_STAGES.includes(s);

export const STAGE_KO: Record<Stage, string> = {
  new: "새 문의",
  drafting: "AI가 분류·초안 작성 중",
  review: "상담원 확인 대기",
  sent: "답변 보냄",
  resolved: "해결",
  escalated: "다른 부서로 이관",
};

/** 상담 목록을 카드로. 진행 중이고 답을 기다리면 확인 대기, 고객 답을 기다리면 답변 보냄 */
export function cardsFromConversations(list: ConversationListItem[]): BoardCard[] {
  return list.map((c) => ({
    id: `c${c.id}`,
    who: c.customer_name,
    subject: c.subject_display || c.subject,
    lang: c.language ?? "ko",
    urgency: c.urgency,
    stage: c.status === "resolved" ? "resolved" : c.status === "escalated" ? "escalated" : c.awaiting_reply ? "review" : "sent",
  }));
}

/** 카드마다 "새 문의 → AI 작성 → 상담원 확인 → 끝난 모양" 4박자를 거치고, 한 건당 2박자씩 늦게 시작한다 */
const BEAT = 2;
const HOLD = 3; // 전부 끝난 뒤 잠깐 멈추는 박자

export function replayLength(n: number) {
  return n * BEAT + 3 + HOLD;
}

/**
 * 재생 장면. tick은 0부터 늘어나며 replayLength에서 처음으로 돌아간다.
 * 순수 함수: 같은 목록·같은 tick이면 같은 결과. 오래된 문의(id가 작은 것)부터 처리한다.
 */
export function replayCards(real: BoardCard[], tick: number): BoardCard[] {
  const sorted = [...real].sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
  const t = tick % replayLength(sorted.length);
  return sorted.map((c, i) => {
    const p = t - i * BEAT;
    const final: Stage = isDone(c.stage) ? c.stage : "sent";
    if (p < 0) return { ...c, stage: "new", worker: undefined };
    if (p === 0) return { ...c, stage: "drafting", worker: "ai" };
    if (p === 1) return { ...c, stage: "review", worker: "human" };
    return { ...c, stage: final, worker: undefined };
  });
}

/** 화면 위쪽 일꾼 띠에 쓰는 한 줄 설명 */
export function workerLine(cards: BoardCard[]) {
  const ai = cards.find((c) => c.worker === "ai");
  const human = cards.find((c) => c.worker === "human");
  return {
    ai: ai ? `${ai.who} 님 문의를 분류하고 답변 초안을 쓰는 중` : "새 문의를 기다리는 중",
    human: human ? `${human.who} 님 답변 초안을 확인하는 중` : "확인할 초안을 기다리는 중",
  };
}
