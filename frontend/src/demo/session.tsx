// 데모 로그인 세션. 실제 인증이 아니다: 데모 계정 + 회원가입한 계정을 브라우저에만 기억한다.
// 회원 역할은 셋이다.
// - 운영자: AXcellent 운영팀. 관제 센터(/control)만 연다
// - 관리자(회사 계정): 고객사 시스템 관리자. 운영자에게 받은 키로 가입하고, 시스템 관리 메뉴와 회사가 켠 서비스를 연다
// - 일반회원: 고객사 직원. 관리자가 계정(이메일)이나 PC IP를 회사에 연결해야 들어간다(members.ts)
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { SERVICES, SHARED_PATHS, getEnabled, matchPath } from './services'
import { getCompany } from './company'
import { memberLinked } from './members'

export type Account = {
  id: string
  name: string
  /** 화면에 보이는 직무 이름. 회사가 정하는 값이라 데모에서는 예시다 */
  role: string
  email: string
  /** 배정된 서비스 id (services.ts) */
  services: string[]
  /** 관리자(회사 계정): 서비스 고르기·회원 연결·회사 서비스 연결을 연다 */
  admin?: boolean
  /** 운영자(AXcellent 운영팀): 관제 센터(/control)만 연다. 고객사 화면에는 들어가지 않는다 */
  operator?: boolean
}

export type MemberKind = '운영자' | '관리자' | '일반회원'
export const kindOf = (a: Account): MemberKind => (a.operator ? '운영자' : a.admin ? '관리자' : '일반회원')
/** 일반회원이 회사에 연결돼 있나 (운영자·관리자는 늘 true) */
export const isLinked = (a: Account) =>
  !!a.operator || !!a.admin || (!!getCompany() && (DEMO_IDS.has(a.id) || memberLinked(a.email)))

// 데모 고객사 계정: 관리자 + 일반회원(시스템 관리 메뉴 없음, 회사에 연결된 상태). 예전 ?as=<직무 id> 링크는 관리자로 들어간다(load)
// 화면에만 있는 계정이라 백엔드 데이터(재무 장부 등)에는 영향이 없다
export const ACCOUNTS: Account[] = [
  { id: 'admin', name: '관리자', role: '관리자', email: 'admin@catbooks.example', services: SERVICES.map((s) => s.id), admin: true },
  { id: 'member', name: '일반회원', role: '일반회원', email: 'member@catbooks.example', services: SERVICES.map((s) => s.id) },
]
const DEMO_IDS = new Set(ACCOUNTS.map((a) => a.id))
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

/** 이미 가입된 이메일인가 (운영자 포함) */
export const emailTaken = (email: string) =>
  [...customerAccounts(), ...OPERATORS].some((a) => a.email === email.trim().toLowerCase())

export type SignupInput = { name: string; email: string; role?: string }
/** 회원가입. 일반회원 가입(/signup)과 관리자 가입(/setup, 운영자에게 받은 키 확인 뒤)이 같이 쓴다 */
export function registerAccount(x: SignupInput, admin = false): Account {
  const email = x.email.trim().toLowerCase()
  if (emailTaken(email)) throw new Error('이미 가입된 이메일입니다.')
  const account: Account = {
    id: `u-${Date.now().toString(36)}`,
    name: x.name.trim(),
    role: x.role?.trim() || (admin ? '관리자' : '일반회원'),
    email,
    // 관리자·일반회원 모두 회사가 켠 서비스(DejaFix 포함)를 쓴다. 차이는 시스템 관리 메뉴뿐
    services: SERVICES.map((s) => s.id),
    ...(admin ? { admin: true } : {}),
  }
  try {
    localStorage.setItem(SIGNUP_KEY, JSON.stringify([...loadSignups(), account]))
  } catch {
    throw new Error('이 브라우저에 계정을 저장할 수 없습니다.')
  }
  return account
}

// 운영자(AXcellent 운영팀) 계정. 고객사 로그인 화면에는 나오지 않고, 관제 로그인(/control/login)에서만 쓴다
export const OPERATORS: Account[] = [
  { id: 'axc', name: 'AXcellent 운영자', role: '운영자', email: 'ops@axcellent.dev', services: [], operator: true },
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
  // 회사 연결 전: 관리자는 AXcellent 연결만, 일반회원은 아무 화면도 못 연다
  if (!connected) return !!account.admin && matchPath('/system/connect', path)
  // 일반회원은 관리자가 계정·PC IP를 회사에 연결해야 연다
  if (!isLinked(account)) return false
  const bases = [
    ...SHARED_PATHS,
    ...(account.admin ? SYSTEM_PATHS : []),
    ...servicesOf(account, enabled).flatMap((s) => s.paths),
  ]
  return bases.some((b) => matchPath(b, path))
}

/** 로그인 뒤 처음 여는 화면: 운영자는 관제 센터, 관리자는 서비스 고르기(연결 전이면 AXcellent 연결), 일반회원은 첫 서비스(없으면 AI 대화방) */
export const homeOf = (account: Account, enabled = getEnabled()) =>
  account.operator ? '/control'
    : account.admin ? (getCompany() ? '/system/services' : '/system/connect')
    : !isLinked(account) ? '/login'
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
