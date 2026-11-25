// 사이드바 숫자: 기획 AI 들어온 요청 수, 개발 AI 사람 확인 작업 수, 총괄 사람 승인 대기 수.
// 화면을 열지 않아도 보이게 30초마다 각 서버 상태(GET /state)를 받아 센다. 서버가 꺼져 있으면 숫자를 지운다.
import { useEffect, useSyncExternalStore } from 'react'
import { orchestrator } from '@/lib/orchestrator'

// 기획·개발 서버 읽기(lib/planning), 기획 요청 화면·개발 작업판 코드는 숫자를 셀 때만 불러온다.
// 정적으로 가져오면 두 화면 코드·CSS가 모든 화면의 첫 묶음에 들어가 첫 화면이 늦어진다 (11/25 Lighthouse)
const loadIncomingCount = () => import('@/components/planning/requests').then((m) => m.incomingCount)
const loadTaskRow = () => import('@/components/dev/board').then((m) => m.taskRow)

const POLL_MS = 30_000
export const PLANNING_REQUESTS_HREF = '/planning/requests'
export const DEV_TASKS_HREF = '/dev/tasks'
export const ORCHESTRATOR_HREF = '/orchestrator'

let counts: Record<string, number> = {}
const listeners = new Set<() => void>()

// 상단 종 알림: 개발 AI 작업 중 사람이 봐야 하는 것 (CI 실패·머지 충돌·자동 머지 안 됨·리뷰 불일치 등)
export interface DevAlert { id: string; title: string; why: string; href: string }
let alerts: DevAlert[] = []

function setAlerts(next: DevAlert[]) {
  const key = (xs: DevAlert[]) => xs.map((a) => `${a.id}|${a.why}`).join(';')
  if (key(next) === key(alerts)) return
  alerts = next
  listeners.forEach((l) => l())
}

function set(href: string, n: number | undefined) {
  if (counts[href] === n) return
  counts = { ...counts }
  if (n) counts[href] = n
  else delete counts[href]
  listeners.forEach((l) => l())
}

async function count(part: 'planning' | 'dev'): Promise<number | undefined> {
  try {
    const { apiBase, fromBackend, planningApi } = await import('@/lib/planning')
    const s = fromBackend(await planningApi(apiBase(part)).state(), [])
    if (part === 'planning') return (await loadIncomingCount())(s)
    const taskRow = await loadTaskRow()
    const stuck = s.tasks.map((t) => taskRow(s, t)).filter((r) => r.group === 'stuck')
    setAlerts(
      stuck.filter((r) => !r.t.discarded).map((r) => ({
        id: r.t.id,
        title: r.t.title,
        why: r.t.coding?.error ?? r.cells[2]?.meta ?? '사람 확인 필요',
        href: r.next.href,
      })),
    )
    return stuck.length
  } catch {
    if (part === 'dev') setAlerts([])
    return undefined
  }
}

/** 총괄 이슈들의 사람 승인 대기 건수. 읽지 못하면 숫자를 지운다 */
async function heldCount(): Promise<number | undefined> {
  try {
    const { data } = await orchestrator.incidents()
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

/** 메뉴 주소 → 숫자. 메뉴에 보이는 서비스만 센다 */
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

/** 개발 AI 알림 목록 (useNavCounts가 개발 서버를 셀 때 같이 채운다) */
export function useDevAlerts(): DevAlert[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => alerts,
  )
}
