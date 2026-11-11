// 1:1 직접 대화를 이어 가기 위한 기억 (AI 대화방 직접 대화). 이 브라우저에만 있어 다른 PC에는 보이지 않는다(비공개).
// 단톡방 대화는 여기를 쓰지 않고 dashboard-api 단톡방 목록(officeRoom.ts)으로 모두에게 보인다.

// 대화 주인 (대화 id → 계정 id). 데모라 이 브라우저에만 저장한다
export const OWNER_KEY = "axcellent-direct-chat-owner";
export const loadOwners = (): Record<string, string> => {
  try {
    const v = JSON.parse(localStorage.getItem(OWNER_KEY) ?? "{}");
    return v && typeof v === "object" ? (v as Record<string, string>) : {};
  } catch { return {}; }
};
export const saveOwner = (threadId: string, accountId: string) => {
  try { localStorage.setItem(OWNER_KEY, JSON.stringify({ ...loadOwners(), [threadId]: accountId })); } catch { /* 저장이 막히면 새로고침 때 내 대화 목록에서 빠진다 */ }
};
// 이 계정이 이 탭에서 이어 가던 대화
export const threadKey = (agentKey: string, accountId: string) => `axcellent-direct-chat-${agentKey}:${accountId}`;
export const loadThreadId = (key: string) => { try { return sessionStorage.getItem(key); } catch { return null; } };
export const saveThreadId = (key: string, id: string | null) => {
  try { if (id) sessionStorage.setItem(key, id); else sessionStorage.removeItem(key); } catch { /* 저장이 막히면 새로고침 때 새 대화 */ }
};
