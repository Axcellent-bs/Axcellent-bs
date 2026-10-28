// 소통 › 에이전트 대화의 원천: dashboard-api 공통 로그 조회
// `GET /api/dashboard/communications?dept=planning` (#248·#277·#278).
import { useEffect, useState } from 'react'
import type { Activity, Dept } from './data'

const API = (import.meta.env.VITE_DASHBOARD_API_URL as string | undefined) ?? 'http://localhost:8020'
const POLL_MS = 5000

export type Outcome = 'completed' | 'rejected' | 'failed' | 'held' | 'needs_human' | 'in_progress'
// 대시보드 AI 대화방(labels.ts COMM_OUTCOME_KO)과 같은 말
export const OUTCOME_KO: Record<Outcome, string> = {
  completed: '완료', rejected: '거절', failed: '실패', held: '보류(안전 관문)', needs_human: '사람 판단 필요', in_progress: '진행 중',
}

interface Line { log_id: string; occurred_at: string; from_agent: string; to_agent?: string | null; skill?: string | null; outcome: Outcome; context_id?: string | null; execution_id?: string | null }

// 에이전트 이름 → 화면 부서. DejaFix는 에이전트 이름이 infra라 그대로 쓴다 (dejafix·infra-dejafix도 받음)
const ALIAS: Record<string, Dept> = { dejafix: 'infra', 'infra-dejafix': 'infra', 'shop-api': 'shop' }
const DEPTS: Dept[] = ['planning', 'hr', 'support', 'marketing', 'data', 'finance', 'infra', 'orchestrator', 'safety', 'shop', 'memory']
const deptOf = (name?: string | null): Dept => {
  const n = (name ?? '').toLowerCase()
  return ALIAS[n] ?? (DEPTS.includes(n as Dept) ? (n as Dept) : 'orchestrator')
}
const pad = (n: number) => String(n).padStart(2, '0')
function kst(iso: string): string {
  const k = new Date(new Date(iso).getTime() + 9 * 3_600_000)
  return `${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())} ${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())}`
}

export function toActivity(l: Line): Activity {
  const skill = l.skill ?? '-'
  return {
    t: kst(l.occurred_at), ctx: l.context_id ?? '사건 전', from: deptOf(l.from_agent), to: deptOf(l.to_agent), point: skill, kind: 'A2A',
    text: `${skill} · ${OUTCOME_KO[l.outcome] ?? l.outcome}${l.execution_id ? ` · ${l.execution_id}` : ''}`,
  }
}

// 공통 로그 줄을 돌려준다 (연결 전에는 빈 목록)
export function useCommunications(dept: Dept = 'planning'): Activity[] | null {
  const [lines, setLines] = useState<Activity[]>([])
  useEffect(() => {
    let alive = true
    const load = () =>
      fetch(`${API}/api/dashboard/communications?dept=${dept}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((body: { data: Line[] }) => { if (alive) setLines(body.data.map(toActivity)) })
        .catch(() => {}) // 연결 전(503 UPSTREAM_NOT_CONNECTED 등)에는 빈 목록을 유지
    load()
    const i = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(i) }
  }, [dept])
  return lines
}
