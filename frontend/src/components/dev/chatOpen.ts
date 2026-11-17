// 작업 카드의 'AI 대화로 넘기기'가 개발 화면 대화 패널을 그 대화로 여는 신호 (chat.tsx ↔ coding.tsx 순환 import를 피해 따로 둔다)
import { useEffect } from 'react'
import type { ChatThread } from '@/lib/planningData'

const OPEN_EVENT = 'ax-open-dev-chat'
export const openDevChat = (t: ChatThread) => window.dispatchEvent(new CustomEvent<ChatThread>(OPEN_EVENT, { detail: t }))
/** 대화를 열라는 신호를 받는다 (패널을 가진 화면이 쓴다) */
export function useOpenDevChat(open: (t: ChatThread) => void) {
  useEffect(() => {
    const on = (e: Event) => open((e as CustomEvent<ChatThread>).detail)
    window.addEventListener(OPEN_EVENT, on)
    return () => window.removeEventListener(OPEN_EVENT, on)
  }, [open])
}
