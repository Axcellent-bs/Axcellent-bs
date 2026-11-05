// 개발이 "코드 변경이 아니다"로 돌려보낸 작업과, 개발에서 폐기한 작업 (기획 화면 표시용).
// 서버: planning domain/returned_actions.py. 처리했는지는 활동(ctx = returned:<작업 id>)으로 가린다.
import type { State, Task } from './planningData'

export const returnCtx = (taskId: string) => `returned:${taskId}`
const HANDLED = new Set(['returned_marketing', 'returned_dev', 'returned_dismissed'])

export const RETURN_DEPT: Record<string, string> = {
  marketing: '마케팅', support: '고객지원', finance: '재무', hr: '인사', data: '데이터분석', other: '다른 부서',
}

/** 사람이 아직 처리하지 않은, 개발이 돌려보낸 작업 (기획서 하나로 좁힐 수 있다) */
export function pendingReturns(s: State, spec?: string): Task[] {
  return s.tasks.filter((t) => t.coding?.status === 'returned' && !t.discarded && (!spec || t.spec === spec)
    && !s.activity.some((a) => a.ctx === returnCtx(t.id) && HANDLED.has(a.point)))
}

/** 이 기획서의 작업을 개발에서 모두 폐기했나 (중단 버튼 대신 "개발에서 폐기함") */
export function devDiscarded(s: State, spec?: string): boolean {
  if (!spec) return false
  const mine = s.tasks.filter((t) => t.spec === spec)
  return mine.length > 0 && mine.every((t) => t.discarded)
}
