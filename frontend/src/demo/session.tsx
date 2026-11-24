// 로그인 세션. 두 갈래다(사람별 토큰 설계 2단계, docs/superpowers/specs/2026-11-19-human-session-tokens-design.md).
// - 서버 로그인: dashboard-api `/auth/status`가 켜져 있다고 하면 이메일·비밀번호를 서버가 확인하고 세션 토큰을 준다
//   (lib/humanSession.ts). 데모 계정 버튼·?as= 바로 들어가기는 막는다. 만료되면 로그인 화면으로 간다
// - 데모 로그인: 서버 로그인이 꺼져 있으면(세션 비밀값 없음, 1~3단계) 지금까지처럼 데모 계정 + 회원가입한 계정을
//   브라우저에만 기억하고 비밀번호는 보지 않는다. 쓰기는 공용 토큰으로 간다
// 회원 역할은 셋이다.
// - 운영자: AXcellent 운영팀. 관제 센터(/control)만 연다. 지원 모드(support.ts)로 들어가면 고객사 화면을 보기 전용으로 연다
// - 관리자(회사 계정): 고객사 시스템 관리자. 운영자에게 받은 키로 가입하고, 시스템 관리 메뉴와 모든 서비스를 연다
// - 일반회원: 고객사 직원. 관리자가 계정(이메일)이나 PC IP를 회사에 연결해야 들어간다(members.ts)
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { SERVICES, SHARED_PATHS, matchPath } from './services'
import { getCompany } from './company'
import { memberLinked, useProfiles, withProfile, type ReaderArea } from './members'
import { endSupport, getSupport, supportActive } from './support'
import { setActorAccount, setActorName } from '@/lib/actor'
import { fetchLoginEnabled, fetchMe, serverLogout } from '@/lib/authApi'
import { getSession, setSession, type HumanSession, type ServerAccount } from '@/lib/humanSession'
import { approverOf, customerRows } from '@/lib/memberAdmin'
import { clearServerMembers, getServerMembers } from './serverMembers'

export type Account = {
  id: string
  name: string
  /** 화면에 보이는 직무 이름. 회사가 정하는 값이라 데모에서는 예시다 */
  role: string
  email: string
  /** 배정된 서비스 id (services.ts) */
  services: string[]
  /** 관리자(회사 계정): 회사 연결·회원 연결·도구 연결을 연다 */
  admin?: boolean
  /** 운영자(AXcellent 운영팀): 관제 센터(/control)만 연다. 고객사 화면은 지원 모드에서 보기 전용으로만 연다 */
  operator?: boolean
  /** 부서. 받은 요청이 이 부서로 온다 */
  dept?: string
  /** 확인 권한: 받은 요청 중 위험한 건(확인 권한 필요)을 승인할 수 있다 */
  approver?: boolean
  /** 열람 담당: 인사·재무 AI의 다른 사람 대화 요약을 본다 (그 밖의 AI는 모두가 본다) */
  readers?: ReaderArea[]
  /** 서버 로그인 계정 (dashboard-api 계정). 회원 연결은 계정을 만든 관리자가 이미 한 것으로 본다 */
  server?: boolean
}

export type MemberKind = '운영자' | '관리자' | '일반회원'
export const kindOf = (a: Account): MemberKind => (a.operator ? '운영자' : a.admin ? '관리자' : '일반회원')
/** 일반회원이 회사에 연결돼 있나 (운영자·관리자는 늘 true) */
export const isLinked = (a: Account) =>
  !!a.operator || !!a.admin || (!!getCompany() && (!!a.server || DEMO_IDS.has(a.id) || memberLinked(a.email)))

/** 로그인 방식: 서버 로그인 / 데모 로그인(서버 로그인이 꺼져 있을 때) / 아직 서버에 묻는 중 */
export type LoginMode = 'checking' | 'server' | 'demo'
let modeNow: LoginMode = 'checking'
export const loginMode = () => modeNow

/** 서버 계정을 화면 계정으로. 역할·부서·확인 권한·열람 담당은 서버 계정 값을 쓴다(관리자가 회원·서버 관리에서 서버에 저장) */
export function accountFromServer(a: ServerAccount): Account {
  const base = {
    id: a.id, name: a.name, email: a.email, server: true,
    ...(a.dept ? { dept: a.dept } : {}),
    readers: a.readers ?? [],
  }
  if (a.role === 'operator') return { ...base, role: '운영자', services: [], operator: true }
  if (a.role === 'admin') return { ...base, role: '관리자', services: SERVICES.map((s) => s.id), admin: true, approver: approverOf(a) }
  return { ...base, role: '일반회원', services: SERVICES.map((s) => s.id), approver: approverOf(a) }
}

// 데모 고객사 계정: 관리자 + 일반회원(시스템 관리 메뉴 없음, 회사에 연결된 상태). 예전 ?as=<직무 id> 링크는 관리자로 들어간다(load)
// 화면에만 있는 계정이라 백엔드 데이터(재무 장부 등)에는 영향이 없다
export const ACCOUNTS: Account[] = [
  { id: 'admin', name: '관리자', role: '관리자', email: 'admin@company.example', services: SERVICES.map((s) => s.id), admin: true, approver: true },
  { id: 'member', name: '일반회원', role: '일반회원', email: 'member@company.example', services: SERVICES.map((s) => s.id), dept: '개발팀', approver: false },
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
/** 로그인할 수 있는 고객사 계정: 데모 계정 + 회원가입한 계정 (관리자가 바꾼 부서·확인 권한을 덮어쓴다) */
export const customerAccounts = (): Account[] => {
  if (modeNow !== 'server') return [...ACCOUNTS, ...loadSignups()].map((a) => withProfile(a))
  // 서버 로그인: 서버 계정 목록(serverMembers.ts)만 쓴다. 브라우저에 기억한 계정·프로필은 보지 않는다.
  // 목록을 아직 못 받았거나 읽을 수 없는 역할(일반회원)이면 지금 로그인한 고객사 계정만
  const me = getSession()?.account
  const rows = getServerMembers().rows ?? (me ? [me] : [])
  return customerRows(rows).map(accountFromServer)
}

/** 이미 가입된 이메일인가 (운영자 포함) */
export const emailTaken = (email: string) =>
  [...customerAccounts(), ...OPERATORS].some((a) => a.email === email.trim().toLowerCase())

export type SignupInput = { name: string; email: string; role?: string; dept?: string }
/** 회원가입. 일반회원 가입(/signup)과 관리자 가입(/setup, 운영자에게 받은 키 확인 뒤)이 같이 쓴다 */
export function registerAccount(x: SignupInput, admin = false): Account {
  const email = x.email.trim().toLowerCase()
  if (emailTaken(email)) throw new Error('이미 가입된 이메일입니다.')
  const account: Account = {
    id: `u-${Date.now().toString(36)}`,
    name: x.name.trim(),
    role: x.role?.trim() || (admin ? '관리자' : '일반회원'),
    email,
    // 관리자·일반회원 모두 모든 서비스(DejaFix 포함)를 쓴다. 차이는 시스템 관리 메뉴뿐
    services: SERVICES.map((s) => s.id),
    ...(admin ? { admin: true } : {}),
    ...(x.dept?.trim() ? { dept: x.dept.trim() } : {}),
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


// 시스템 관리 화면은 관리자만. 도구 연결(/system/tools)은 SHARED_PATHS에 있어 모두 연다
const SYSTEM_PATHS = ['/system']

/** 이 계정이 지금 쓸 수 있는 서비스: AI 서비스 전부(모두 필수) + 이 계정에 배정된 그 밖의 서비스(DejaFix 등) */
export const servicesOf = (account: Account) =>
  SERVICES.filter((s) => s.agent !== undefined || account.services.includes(s.id))

/** 이 계정에 배정된 서비스. 운영팀이 아닌 계정의 첫 화면은 이걸로 정한다 */
export const assignedOf = (account: Account) =>
  SERVICES.filter((s) => account.services.includes(s.id))

/** 운영자가 지원 모드에서 고객사 화면을 볼 때의 모양: 그 회사 관리자와 같은 화면을 연다(바꾸는 버튼은 AdminShell이 끈다) */
export const supportViewer = (account: Account): Account => ({ ...account, operator: false, admin: true, services: SERVICES.map((s) => s.id) })
/** 이 운영자가 지금 지원 모드로 고객사에 들어가 있나 */
export const isSupporting = (account: Account) => !!account.operator && supportActive(getSupport(), getCompany())

export function canOpen(account: Account, path: string, connected = !!getCompany()): boolean {
  if (account.operator) {
    if (matchPath('/control', path)) return true
    return isSupporting(account) && canOpen(supportViewer(account), path, connected)
  }
  // 소속 회사가 없는 일반회원(연결 대기)은 아무 화면도 못 연다
  if (waiting(account)) return false
  // 회사 연결 전: 관리자는 AXcellent 연결만, 일반회원은 아무 화면도 못 연다
  if (!connected) return !!account.admin && matchPath('/system/connect', path)
  // 일반회원은 관리자가 계정·PC IP를 회사에 연결해야 연다
  if (!isLinked(account)) return false
  const bases = [
    ...SHARED_PATHS,
    ...(account.admin ? SYSTEM_PATHS : []),
    ...servicesOf(account).flatMap((s) => s.paths),
  ]
  return bases.some((b) => matchPath(b, path))
}

/** 로그인 뒤 처음 여는 화면: 운영자는 관제 센터, 관리자는 AI 대화방(연결 전이면 AXcellent 연결), 일반회원은 첫 서비스(없으면 AI 대화방) */
export const ADMIN_HOME = '/agents/chat'
/** 회사에 연결되지 않은 일반회원(관리자가 아직 연결 안 했거나 끊음)이 보는 화면. 할 수 있는 일이 없다 */
export const WAITING_PATH = '/waiting'
/** 서버 로그인 일반회원인데 소속 회사가 없다(관리자가 연결하지 않았거나 끊음) */
export const waiting = (account: Account) =>
  !account.operator && !account.admin && !!account.server && !!getSession()?.noCompany
export const homeOf = (account: Account) =>
  account.operator ? '/control'
    : account.admin ? (getCompany() ? ADMIN_HOME : '/system/connect')
    : waiting(account) ? WAITING_PATH
    : !isLinked(account) ? '/login'
    : assignedOf(account)[0]?.entry ?? '/agents/chat'

const KEY = 'axcellent-demo-account'
/** 데모 로그인으로 들어갈 계정: ?as=<계정 id> 또는 지난번 계정. 서버 로그인이 꺼져 있다고 확인된 뒤에만 쓴다 */
const demoCandidate = (): Account | null => {
  // ?as=<계정 id> 로 열면 그 계정으로 바로 들어간다 (팀원에게 화면 링크를 줄 때)
  const as = new URLSearchParams(window.location.search).get('as')
  // 없어진 직무 계정 id(infra·support 등)로 와도 시스템 관리자로 연다
  if (as) return all().find((a) => a.id === as) ?? ADMIN
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
const fromSession = (s: HumanSession | null) => (s ? accountFromServer(s.account) : null)

type Session = {
  account: Account | null
  /** 서버 로그인 / 데모 로그인 / 확인 중 */
  mode: LoginMode
  /** 서버 세션이 만료·종료돼 로그아웃됐다 (로그인 화면에 안내) */
  expired: boolean
  /** 데모 로그인. 서버 로그인이 켜져 있으면 들어가지 않는다(null) */
  signIn: (id: string) => Account | null
  /** 서버 로그인 응답의 세션으로 들어간다 */
  signInServer: (s: HumanSession) => Account
  /** 로그인하려 했더니 서버가 꺼져 있다고 했다(503 LOGIN_DISABLED) → 데모 로그인으로 바꾼다 */
  fallBackToDemo: () => void
  signOut: () => void
  /** 서버 로그인: 내 계정 값(`/auth/me`)을 다시 읽어 화면 권한에 쓴다. 데모 로그인이면 아무것도 안 한다 */
  refreshMe: () => Promise<void>
}
const Ctx = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<LoginMode>(() => (getSession() ? 'server' : 'checking'))
  const [account, setAccount] = useState<Account | null>(() => fromSession(getSession()))
  const [expired, setExpired] = useState(false)
  modeNow = mode

  // 서버 로그인이 켜져 있는지 묻는다. 꺼져 있으면 데모 로그인(?as=·지난 계정)으로 들어간다
  useEffect(() => {
    let alive = true
    void fetchLoginEnabled().then((on) => {
      if (!alive) return
      if (on) {
        setMode('server')
        save(null) // 서버 로그인이 켜진 뒤에는 예전 데모 계정으로 들어가지 않는다
        return
      }
      if (getSession()) return // 받은 세션은 만료까지 쓴다(그 사이 서버가 꺼졌어도 토큰 검사는 서버가 한다)
      setMode('demo')
      const demo = demoCandidate()
      if (demo) {
        save(demo.id)
        setAccount((cur) => cur ?? demo)
      }
    })
    return () => {
      alive = false
    }
  }, [])

  const endServerSession = useCallback(() => {
    clearServerMembers()
    setSession(null)
    setAccount(null)
    setExpired(true)
  }, [])

  // 세션 만료 시각에 로그아웃하고, 처음 열 때는 서버가 아직 이 세션을 받는지(로그아웃·계정 삭제) 본다
  const session = account?.server ? getSession() : null
  const expiresAt = session?.expiresAt
  useEffect(() => {
    if (expiresAt === undefined) return
    const ms = expiresAt * 1000 - Date.now()
    if (ms <= 0) return endServerSession()
    const t = setTimeout(endServerSession, Math.min(ms, 2 ** 31 - 1))
    return () => clearTimeout(t)
  }, [expiresAt, endServerSession])
  // 서버 계정 값을 다시 읽는다(`/auth/me`): 관리자가 부서·확인 권한·열람 담당을 바꾸면 이 화면 권한에도 바로 쓴다.
  // 역할이 바뀌면 서버가 세션을 끊으므로 401 → 로그아웃
  const refreshMe = useCallback(async () => {
    if (!getSession()) return
    const r = await fetchMe()
    if (r.kind === 'ended') return endServerSession()
    if (r.kind !== 'alive') return
    const s = getSession()
    if (!s || s.account.id !== r.account.id) return
    setSession({ ...s, account: r.account })
    setAccount(accountFromServer(r.account))
  }, [endServerSession])
  // 처음 열 때와 탭으로 돌아올 때 본다
  useEffect(() => {
    void refreshMe()
    const onFocus = () => void refreshMe()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshMe])

  const signIn = useCallback((id: string) => {
    if (modeNow === 'server') return null
    const next = all().find((a) => a.id === id) ?? null
    setAccount(next)
    save(next?.id ?? null)
    return next
  }, [])
  const signInServer = useCallback((s: HumanSession) => {
    clearServerMembers()
    setSession(s)
    setMode('server')
    setExpired(false)
    save(null)
    const next = accountFromServer(s.account)
    setAccount(next)
    return next
  }, [])
  const fallBackToDemo = useCallback(() => {
    modeNow = 'demo'
    setMode('demo')
  }, [])
  const signOut = useCallback(() => {
    if (getSession()) {
      void serverLogout().finally(() => setSession(null))
    }
    setAccount(null)
    setExpired(false)
    save(null)
    clearServerMembers()
    endSupport()
  }, [])
  // 데모 로그인: 관리자가 바꾼 부서·확인 권한(브라우저에 기억)을 로그인한 계정에도 바로 보인다.
  // 서버 로그인: 서버 계정 값(`/auth/me`)을 그대로 쓰고 브라우저 프로필은 덮어쓰지 않는다
  const profiles = useProfiles()
  const current = useMemo(
    () => (account ? (account.server ? account : withProfile(account, profiles)) : null),
    [account, profiles],
  )
  // 사람 결정 요청에 같이 보낼 계정 이름 (재무 기록의 "관리자(IP)" 표시용, lib/actor.ts). 같은 값을 다시 넣을 뿐이라 렌더 중에 둔다
  setActorName(current?.name)
  setActorAccount(current?.id)
  const value = useMemo(
    () => ({ account: current, mode, expired, signIn, signInServer, fallBackToDemo, signOut, refreshMe }),
    [current, mode, expired, signIn, signInServer, fallBackToDemo, signOut, refreshMe],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useSession() {
  const s = useContext(Ctx)
  if (!s) throw new Error('SessionProvider 밖에서 useSession을 불렀습니다')
  return s
}
