// 개발 화면 "AI와 대화" 패널 규칙 (화면 없이 시험할 수 있게 뺀 것). node --test로 돈다 (devChatView.test.ts)
import type { ChatThread } from './planningData'

/** SSE 상태 갱신 순서 지키기: 늦게 끝난 오래된 갱신이 최신을 덮어쓰지 않게 한다.
 *  `start()`로 받은 번호를 적용 직전 `fresh(n)`에 넘긴다. 이미 더 최신을 적용했으면 false (버린다) */
export function createLatestGate() {
  let issued = 0
  let applied = 0
  return {
    start: () => ++issued,
    fresh: (n: number) => {
      if (n <= applied) return false
      applied = n
      return true
    },
  }
}

/** 같은 대화는 더 최근 것(시각이 늦은 것, 같으면 메시지 많은 것)을 쓴다. 최근 순으로 준다 */
export function mergeThreads(a: ChatThread[], b: ChatThread[]): ChatThread[] {
  const m = new Map<string, ChatThread>()
  for (const t of [...a, ...b]) {
    const o = m.get(t.id)
    if (!o || t.updatedAt > o.updatedAt || (t.updatedAt === o.updatedAt && t.messages.length > o.messages.length)) m.set(t.id, t)
  }
  return [...m.values()].sort((x, y) => (y.updatedAt > x.updatedAt ? 1 : y.updatedAt < x.updatedAt ? -1 : 0))
}

export const LIVE_ANSWER_POLL_MS = 4000 // SSE가 있어도 답을 기다리는 동안은 이 간격으로 다시 읽는다
export const POLL_MS = 2000 // SSE가 없을 때 다시 읽는 간격

/** 선택한 대화를 다시 읽을 간격. null이면 읽지 않는다.
 *  SSE가 없으면 답을 기다리거나 작업이 도는 동안 2초, SSE가 있어도 답을 기다리는 동안은 4초
 *  (SSE 갱신을 놓치거나 순서가 꼬여 "생각 중"이 안 풀리는 일 막기) */
export function pollInterval(live: boolean, answering: boolean, working: boolean): number | null {
  if (!live) return answering || working ? POLL_MS : null
  return answering ? LIVE_ANSWER_POLL_MS : null
}

// 백엔드 dev_chat.KEY_HINT와 같은 글 (키가 없거나 틀려 AI가 거절한 오류 메시지에 붙는다)
export const KEY_HINT = '서버 AI 키 확인 필요 (도구 연결 > 고객사 키)'

/** 이 오류에 도구 연결 버튼을 붙이나 (AI 키·레포 연결 문제) */
export function needsTools(code: string | null | undefined, text = ''): boolean {
  return code === 'LLM_NOT_CONFIGURED' || code === 'NO_VCS' || text.includes(KEY_HINT)
}

/** 보내기 오류 코드 → 화면 문구 */
export function sendErrorText(code: string, fallback: string): string {
  if (code === 'LLM_NOT_CONFIGURED') return `서버 AI가 설정되지 않았어요. ${KEY_HINT}`
  if (code === 'NO_VCS') return '연결된 레포 없음'
  if (code === 'THREAD_BUSY') return '앞 말에 답하는 중이에요. 답이 온 뒤 보내 주세요'
  if (code === 'THREAD_CLOSED') return '끝난 대화예요. 새 대화에서 보내 주세요'
  return fallback
}

/** 패널 위에 띄울 안내: AI 미설정(보내기 막음)·레포 연결 없음(대화는 됨).
 *  모르면(설정·연결 목록을 아직 못 읽음) 안내하지 않는다 */
export type ChatNotice = 'no_llm' | 'no_repo' | null
export function chatNotice(modelDefault: string | null | undefined, conns: number | null): ChatNotice {
  if (modelDefault === null) return 'no_llm'
  if (conns === 0) return 'no_repo'
  return null
}

/** 끝내기 버튼: 답하는 중·이미 끝난 대화는 못 끝낸다 (서버 THREAD_BUSY) */
export const canClose = (t: ChatThread | undefined) => !!t && t.status !== 'closed' && t.status !== 'answering'

/** 숨긴 대화는 화면에 넣지 않는다 (SSE 상태에는 기록으로 남아 같이 실려 온다) */
export const isShown = (t: { removedAt?: string | null }) => !t.removedAt
