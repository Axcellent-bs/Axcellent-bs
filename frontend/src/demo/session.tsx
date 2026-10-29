// 데모 로그인 세션. 실제 인증이 아니다: 계정은 아래 목록에 고정돼 있고, 고른 계정을 브라우저에만 기억한다.
// 고객사 계정은 시스템 관리자 하나다. 회사가 켠 서비스(services.ts)와 시스템 관리 메뉴를 전부 연다.
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

// 고객사 계정. 직무별 예시 계정(인프라·CS·재무 등)은 없앴다. 예전 ?as=<직무 id> 링크는 시스템 관리자로 들어간다(load)
export const ACCOUNTS: Account[] = [
  { id: 'admin', name: '시스템 관리자', role: '시스템 관리자', email: 'admin@catbooks.example', services: SERVICES.map((s) => s.id), admin: true },
]
const ADMIN = ACCOUNTS[0]

// 회원가입으로 만든 고객사 계정 (데모: 이 브라우저에만 저장, 비밀번호는 저장하지 않는다)
const SIGNUP_KEY = 'axcellent-demo-signups'
const loadSignups = (): Account[] => {
  try {
    const rows = JSON.parse(localStorage.getItem(SIGNUP_KEY) ?? '[]')
    return Array.isArray(rows) ? (rows as Account[]).filter((a) => a && typeof a.id === 'string' && typeof a.email === 'string') : []
  } catch {
    return []
  }
}
/** 로그인할 수 있는 고객사 계정: 데모 계정 + 회원가입한 계정 */
export const customerAccounts = (): Account[] => [...ACCOUNTS, ...loadSignups()]

export type SignupInput = { name: string; email: string; role: string }
/** 회원가입. 회사 연결 전 첫 가입은 시스템 관리자(회사 연결을 맡는다), 연결 뒤에는 직원 계정이다 */
export function registerAccount(x: SignupInput): Account {
  const email = x.email.trim().toLowerCase()
  if ([...customerAccounts(), ...OPERATORS].some((a) => a.email === email)) throw new Error('이미 가입된 이메일입니다.')
  const admin = !getCompany()
  const account: Account = {
    id: `u-${Date.now().toString(36)}`,
    name: x.name.trim(),
    role: x.role.trim() || (admin ? '시스템 관리자' : '직원'),
    email,
    services: admin ? SERVICES.map((s) => s.id) : [],
    ...(admin ? { admin: true } : {}),
  }
  try {
    localStorage.setItem(SIGNUP_KEY, JSON.stringify([...loadSignups(), account]))
  } catch {
    throw new Error('이 브라우저에 계정을 저장할 수 없습니다.')
  }
  return account
}

// AXcellent 운영팀 계정. 고객사 로그인 화면에는 나오지 않고, 관제 로그인(/control/login)에서만 쓴다
export const OPERATORS: Account[] = [
  { id: 'axc', name: 'AXcellent 운영팀', role: 'AXcellent 운영팀', email: 'ops@axcellent.dev', services: [], operator: true },
]
const all = () => [...customerAccounts(), ...OPERATORS]


const SYSTEM_PATHS = ['/system']

/** 이 계정이 지금 쓸 수 있는 서비스: 회사가 켠 AI 서비스 전부 + 이 계정에 배정된 그 밖의 서비스(DejaFix 등) */
export const servicesOf = (account: Account, enabled = getEnabled()) =>
  SERVICES.filter((s) => enabled.includes(s.id) && (s.agent !== undefined || account.services.includes(s.id)))

/** 이 계정에 배정된 서비스(회사가 켠 것 중). 운영팀이 아닌 계정의 첫 화면은 이걸로 정한다 */
export const assignedOf = (account: Account, enabled = getEnabled()) =>
  SERVICES.filter((s) => account.services.includes(s.id) && enabled.includes(s.id))

export function canOpen(account: Account, path: string, enabled = getEnabled(), connected = !!getCompany()) {
  if (account.operator) return matchPath('/control', path)
  // 회사 연결 전: 시스템 관리자는 AXcellent 연결만, 직원은 아무 화면도 못 연다
  if (!connected) return !!account.admin && matchPath('/system/connect', path)
  const bases = [
    ...SHARED_PATHS,
    ...(account.admin ? SYSTEM_PATHS : []),
    ...servicesOf(account, enabled).flatMap((s) => s.paths),
  ]
  return bases.some((b) => matchPath(b, path))
}

/** 로그인 뒤 처음 여는 화면: 운영팀은 관제 센터, 고객사 시스템 관리자는 서비스 고르기(연결 전이면 AXcellent 연결), 나머지는 첫 서비스(없으면 AI 대화방) */
export const homeOf = (account: Account, enabled = getEnabled()) =>
  account.operator ? '/control'
    : account.admin ? (getCompany() ? '/system/services' : '/system/connect')
    : !getCompany() ? '/login'
    : assignedOf(account, enabled)[0]?.entry ?? '/agents/chat'

const KEY = 'axcellent-demo-account'
const load = (): Account | null => {
  // ?as=<계정 id> 로 열면 그 계정으로 바로 들어간다 (팀원에게 화면 링크를 줄 때)
  const as = new URLSearchParams(window.location.search).get('as')
  // 없어진 직무 계정 id(infra·support 등)로 와도 시스템 관리자로 연다
  const direct = as ? all().find((a) => a.id === as) ?? ADMIN : null
  if (direct) {
    save(direct.id)
    return direct
  }
  try {
    const id = localStorage.getItem(KEY)
    return id ? all().find((a) => a.id === id) ?? ADMIN : null
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
    const next = all().find((a) => a.id === id) ?? null
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
