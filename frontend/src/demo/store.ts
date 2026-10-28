// 시스템 관리 화면(PC·서버 연결, 회사 서비스 연결)의 데모 데이터. 브라우저에만 저장하고, 저장이 막혀 있으면 기본값으로 돈다.
import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'

export type Host = {
  id: string
  dept: string
  hostname: string
  ip: string
  status: 'online' | 'offline' | 'pending'
  agent: string
  lastSeen: string
}

export type Integration = {
  id: string
  name: string
  kind: string
  url: string
  apiBase: string
  apiKey: string
  scopes: string[]
  status: 'connected' | 'untested' | 'failed'
  lastSync: string | null
}

export const SCOPES = ['주문', '결제', '고객 문의', '리뷰', '정산', '배포 기록']

const now = () => new Date().toISOString()
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

const DEFAULT_HOSTS: Host[] = [
  { id: 'h0', dept: '인프라팀', hostname: 'dejafix-srv-01', ip: '192.168.10.10', status: 'online', agent: 'DejaFix 0.9', lastSeen: minutesAgo(0) },
  { id: 'h1', dept: '기획팀', hostname: 'plan-pc-01', ip: '192.168.10.21', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(0) },
  { id: 'h7', dept: '개발팀', hostname: 'dev-pc-01', ip: '192.168.10.22', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(0) },
  { id: 'h2', dept: '고객지원팀', hostname: 'cs-pc-01', ip: '192.168.10.31', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(1) },
  { id: 'h3', dept: '마케팅팀', hostname: 'mkt-pc-01', ip: '192.168.10.41', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(0) },
  { id: 'h4', dept: '인사팀', hostname: 'hr-pc-01', ip: '192.168.10.51', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(2) },
  { id: 'h8', dept: '운영팀', hostname: 'ops-pc-01', ip: '192.168.10.52', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(1) },
  { id: 'h5', dept: '재무팀', hostname: 'fin-pc-01', ip: '192.168.10.61', status: 'online', agent: 'AXcellent 0.9', lastSeen: minutesAgo(0) },
  { id: 'h6', dept: '데이터분석팀', hostname: 'data-pc-01', ip: '192.168.10.71', status: 'offline', agent: 'AXcellent 0.9', lastSeen: minutesAgo(47) },
]

const DEFAULT_INTEGRATIONS: Integration[] = [
  {
    id: 'i1', name: '캣북스 온라인 서점', kind: '쇼핑몰', url: 'https://shop.catbooks.example', apiBase: 'https://shop.catbooks.example/api',
    apiKey: 'cb_live_demo_7f3a', scopes: ['주문', '결제', '고객 문의', '리뷰', '정산'], status: 'connected', lastSync: minutesAgo(3),
  },
]

function usePersisted<T>(key: string, fallback: T): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw ? (JSON.parse(raw) as T) : fallback
    } catch {
      return fallback
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* 저장이 막힌 브라우저는 새로고침하면 기본값으로 돌아간다 */
    }
  }, [key, value])
  return [value, setValue]
}

export const useHosts = () => usePersisted('axcellent-demo-hosts-v4', DEFAULT_HOSTS)
export const useIntegrations = () => usePersisted('axcellent-demo-integrations', DEFAULT_INTEGRATIONS)
export const newId = () => Math.random().toString(36).slice(2, 9)
export { now }

/** 사내망 주소(10/8, 172.16/12, 192.168/16)만 받는다. 외부 주소는 연결하지 않는다 */
export function privateIp(ip: string) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip.trim())
  if (!m) return false
  const [a, b] = [Number(m[1]), Number(m[2])]
  if (m.slice(1).some((x) => Number(x) > 255)) return false
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
}
