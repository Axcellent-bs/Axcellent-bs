// 데모 로그인 세션. 실제 인증이 아니다: 계정은 아래 목록에 고정돼 있고, 고른 계정을 브라우저에만 기억한다.
// 부서는 코드에 두지 않는다. 계정마다 쓸 서비스를 배정하고, 회사가 켠 서비스 중 배정된 것만 열린다(services.ts).
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { SERVICES, SHARED_PATHS, getEnabled, matchPath } from './services'
import { getCompany } from './company'

export type Account = {
  id: string
  name: string
  /** 화면에 보이는 직무 이름. 회사가 정하는 값이라 데모에서는 예시다 */
  role: string
  email: string
  /** 배정된 서비스 id (services.ts) */
  services: string[]
  /** 고객사 시스템 관리자: 서비스 고르기·회사 서비스 연결을 연다 */
  admin?: boolean
  /** AXcellent 운영팀(우리 개발자): 관제 센터(/control)만 연다. 고객사 화면에는 들어가지 않는다 */
  operator?: boolean
}

// 예시 직원. ?as=<id> 링크가 그대로 동작하도록 id는 예전 값을 둔다
export const ACCOUNTS: Account[] = [
  { id: 'admin', name: '시스템 관리자', role: '시스템 관리자', email: 'admin@catbooks.example', services: ['orchestrator'], admin: true },
  { id: 'infra', name: '인프라 엔지니어', role: '인프라 엔지니어', email: 'infra@catbooks.example', services: ['incident', 'hosts'] },
  { id: 'dev', name: '개발자', role: '개발자', email: 'dev@catbooks.example', services: ['dev', 'incident'] },
  { id: 'planning', name: '기획자', role: '기획자', email: 'planning@catbooks.example', services: ['planning', 'decisions'] },
  { id: 'support', name: 'CS 상담원', role: 'CS 상담원', email: 'support@catbooks.example', services: ['inquiry', 'claims', 'reviews'] },
  { id: 'marketing', name: '마케터', role: '마케터', email: 'marketing@catbooks.example', services: ['coupon', 'data'] },
  { id: 'finance', name: '재무 담당', role: '재무 담당', email: 'finance@catbooks.example', services: ['finance', 'claims'] },
  { id: 'hr', name: '인사 담당', role: '인사 담당', email: 'hr@catbooks.example', services: ['handover', 'decisions'] },
  { id: 'data', name: '데이터 분석가', role: '데이터 분석가', email: 'data@catbooks.example', services: ['data'] },
]

// AXcellent 운영팀 계정. 고객사 로그인 화면·프로필 전환에는 나오지 않고, 관제 로그인(/control/login)에서만 쓴다
export const OPERATORS: Account[] = [
  { id: 'axc', name: 'AXcellent 운영팀', role: 'AXcellent 운영팀', email: 'ops@axcellent.dev', services: [], operator: true },
]
const ALL = [...ACCOUNTS, ...OPERATORS]


const SYSTEM_PATHS = ['/system']

/** 이 계정이 지금 쓸 수 있는 서비스: 배정됐고 회사가 켠 것 */
export const servicesOf = (account: Account, enabled = getEnabled()) =>
  SERVICES.filter((s) => account.services.includes(s.id) && enabled.includes(s.id))

export function canOpen(account: Account, path: string, enabled = getEnabled()) {
  if (account.operator) return matchPath('/control', path)
  const bases = [
    ...SHARED_PATHS,
    ...(account.admin ? SYSTEM_PATHS : []),
    ...servicesOf(account, enabled).flatMap((s) => s.paths),
  ]
  return bases.some((b) => matchPath(b, path))
}

/** 로그인 뒤 처음 여는 화면: 운영팀은 관제 센터, 고객사 시스템 관리자는 서비스 고르기(연결 전이면 AXcellent 연결), 나머지는 첫 서비스(없으면 AI 대화방) */
export const homeOf = (account: Account, enabled = getEnabled()) =>
  account.operator ? '/control' : account.admin ? (getCompany() ? '/system/services' : '/system/connect') : servicesOf(account, enabled)[0]?.entry ?? '/agents/chat'

const KEY = 'axcellent-demo-account'
const load = (): Account | null => {
  // ?as=<계정 id> 로 열면 그 계정으로 바로 들어간다 (팀원에게 화면 링크를 줄 때)
  const as = new URLSearchParams(window.location.search).get('as')
  const direct = ALL.find((a) => a.id === as)
  if (direct) {
    save(direct.id)
    return direct
  }
  try {
    const id = localStorage.getItem(KEY)
    return ALL.find((a) => a.id === id) ?? null
  } catch {
    return null
  }
}
const save = (id: string | null) => {
  try {
    if (id) localStorage.setItem(KEY, id)
    else localStorage.removeItem(KEY)
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 다시 로그인한다 */
  }
}

type Session = { account: Account | null; signIn: (id: string) => Account | null; signOut: () => void }
const Ctx = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(load)
  const signIn = useCallback((id: string) => {
    const next = ALL.find((a) => a.id === id) ?? null
    setAccount(next)
    save(next?.id ?? null)
    return next
  }, [])
  const signOut = useCallback(() => {
    setAccount(null)
    save(null)
  }, [])
  const value = useMemo(() => ({ account, signIn, signOut }), [account, signIn, signOut])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useSession() {
  const s = useContext(Ctx)
  if (!s) throw new Error('SessionProvider 밖에서 useSession을 불렀습니다')
  return s
}
