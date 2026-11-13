// 사이드바 숫자: 기획 AI 들어온 요청 수, 개발 AI 사람 확인 작업 수, 총괄 사람 승인 대기 수.
// 화면을 열지 않아도 보이게 30초마다 각 서버 상태(GET /state)를 받아 센다. 서버가 꺼져 있으면 숫자를 지운다.
import { useEffect, useSyncExternalStore } from 'react'
import { apiBase, fromBackend, planningApi } from '@/lib/planning'
import { incomingCount } from '@/components/planning/requests'
import { taskRow } from '@/components/dev/board'
import { adminRelay, relayEnabled } from '@/lib/adminRelay'

const POLL_MS = 30_000
export const PLANNING_REQUESTS_HREF = '/planning/requests'
export const DEV_TASKS_HREF = '/dev/tasks'
export const ORCHESTRATOR_HREF = '/orchestrator'

let counts: Record<string, number> = {}
const listeners = new Set<() => void>()

function set(href: string, n: number | undefined) {
  if (counts[href] === n) return
  counts = { ...counts }
  if (n) counts[href] = n
  else delete counts[href]
  listeners.forEach((l) => l())
}

async function count(part: 'planning' | 'dev'): Promise<number | undefined> {
  try {
    const s = fromBackend(await planningApi(apiBase(part)).state(), [])
    return part === 'planning' ? incomingCount(s) : s.tasks.filter((t) => taskRow(s, t).group === 'stuck').length
  } catch {
    return undefined
  }
}

/** 총괄 이슈들의 사람 승인 대기 건수. 읽지 못하면 숫자를 지운다 */
async function heldCount(): Promise<number | undefined> {
  if (!relayEnabled) return undefined
  try {
    const { data } = await adminRelay.incidents()
    return data.reduce((n, i) => n + i.pending_holds.length, 0)
  } catch {
    return undefined
  }
}

async function refresh(planning: boolean, dev: boolean, orchestrator: boolean) {
  if (planning) set(PLANNING_REQUESTS_HREF, await count('planning'))
  if (dev) set(DEV_TASKS_HREF, await count('dev'))
  if (orchestrator) set(ORCHESTRATOR_HREF, await heldCount())
}

/** 메뉴 주소 → 숫자. 켠 서비스만 센다 */
export function useNavCounts(planning: boolean, dev: boolean, orchestrator: boolean): Record<string, number> {
  useEffect(() => {
    if (!planning && !dev && !orchestrator) return
    void refresh(planning, dev, orchestrator)
    const t = setInterval(() => void refresh(planning, dev, orchestrator), POLL_MS)
    return () => clearInterval(t)
  }, [planning, dev, orchestrator])
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => counts,
  )
}
