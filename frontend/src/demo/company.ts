// 이 AXcellent에 연결된 고객사와 AXcellent가 발급한 연결 키. 서버(dashboard-api `connection_keys.py`)에 저장해
// 어느 PC에서든 같은 키 목록·연결 회사가 보인다(예전에는 브라우저에만 있어서 다른 PC는 비어 있었다).
// - 키 발급: AXcellent 운영팀이 관제 센터(/control/keys)에서 한다. 키에는 회사 정보가 없고 "연결해도 된다"는 권한만 있다.
//   전체 키는 발급할 때 한 번만 받는다. 목록에는 가린 키만 온다.
// - 연결: 고객사 시스템 관리자가 키를 넣고(서버가 확인), 회사 정보를 입력해 연결한다(/system/connect). 끊는 것도 고객사가 한다.
// 상단바의 "AXcellent with <회사>"와 로그인 화면이 연결된 회사를 따른다. 끊으면 null(회사 이름 없음).
// 서버는 읽기에도 사람 토큰을 요구한다. 로그인 전에는 회사 공개 정보(이름만, publicCompany.ts)로 로그인 화면을 그리고,
// 로그인·로그아웃하면(세션이 바뀌면) 다시 읽는다. 예전처럼 처음 한 번만 읽으면 로그인 전 401·403으로 회사가 비어
// 로그인한 일반회원이 아무 화면도 못 연다(session.tsx canOpen).
import { useSyncExternalStore } from 'react'
import { call, companyConnection } from '@/lib/companyConnection'
import { DASHBOARD_API_URL } from '@/lib/env'
import { getSession, onSessionChange } from '@/lib/humanSession'
import { pickCompany, publicFallbackOk, readPublicCompany, readTurns, refusedForThisPerson } from '@/lib/publicCompany'

export type ConnectedCompany = {
  id: string
  name: string
  industry: string
  network: string
  /** 회사 사이트 주소(머리글 파비콘·회사 이름 링크). 없으면 빈 값 */
  siteUrl?: string
  /** 연결에 쓴 키(가린 값)와 연결한 시각 */
  key?: string
  connectedAt?: string
}
export type CompanyInfo = Pick<ConnectedCompany, 'name' | 'industry' | 'network' | 'siteUrl'>
export type KeyStatus = 'issued' | 'connected' | 'revoked'
export type IssuedKey = {
  id: string
  /** 목록에서는 가린 키. 발급 직후 응답만 전체 키 */
  key: string
  /** 운영팀 메모(누구에게 보냈는지). 키에는 들어가지 않는다 */
  memo?: string
  issuedAt: string
  status: KeyStatus
  /** 고객사가 연결하면서 입력한 회사 정보 (연결 전에는 없음) */
  company?: ConnectedCompany
  /** 회사 전용 키면 그 회사 ID(발급 때 회사를 등록한 키). 예전 키는 없음 */
  forCompany?: string
  /** 회사 전용 키로 첫 관리자 가입·회사 옮기기를 할 수 있나 (키 확인 응답에서만) */
  signup?: boolean
}
/** 키 발급 때 함께 등록하는 회사. 이름을 넣으면 회사 목록에 새 회사가 생긴다(ID 비우면 자동) */
export type NewCompany = { companyName: string; industry: string; siteUrl?: string; companyId?: string }

type ServerInfo = { name: string; industry: string; network: string; site_url?: string }
type ServerRow = { id: string; key: string; memo: string | null; issued_at: string; status: KeyStatus; company: ServerInfo | null; for_company?: string | null }
type ServerCompany = ServerInfo & { id: string; key: string | null; connected_at: string }
type ServerState = { keys: ServerRow[]; company: ServerCompany | null; editable: boolean }

const PATH = '/api/dashboard/connection-keys'
let keys: IssuedKey[] = []
let company: ConnectedCompany | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

const fromServer = (c: ServerInfo): CompanyInfo => ({ name: c.name, industry: c.industry, network: c.network, siteUrl: c.site_url || undefined })
const toServer = (c: CompanyInfo) => ({ name: c.name, industry: c.industry, network: c.network, site_url: c.siteUrl ?? '' })

// 이 사람 토큰으로 연결 키 목록을 읽었나. 읽었으면 서버가 준 세션 회사의 줄이 답이라 로그인 전 공개 정보로 채우지 않는다
// (공개 정보는 세션이 없어 늘 캣북스. 다른 회사 관리자 화면에 캣북스 이름이 보이지 않게, 설계 6.2)
let keysRead = false
// 로그인한 사람의 키 목록 응답을 기다리는 중인가. 기다리는 동안(연결 실패로 못 받은 때 포함)은 공개 정보(늘 캣북스)로
// 채우지 않는다. 세션이 바뀌면 앞 사람 값을 바로 비우고 새 응답을 기다린다(사후 리뷰 11/23)
let waiting = (() => { try { return !!getSession() } catch { return false } })()
// 세션 차례. 세션이 바뀐 뒤 늦게 온 앞 사람 응답은 버린다
const turns = readTurns()
function apply(s: ServerState) {
  keysRead = true
  waiting = false
  company = s.company
    ? { id: s.company.id, name: s.company.name, industry: s.company.industry, network: s.company.network, siteUrl: s.company.site_url || undefined, key: s.company.key ?? undefined, connectedAt: s.company.connected_at }
    : null
  keys = s.keys.map((r) => ({
    id: r.id,
    key: r.key,
    memo: r.memo ?? undefined,
    issuedAt: r.issued_at,
    status: r.status,
    company: r.company ? { id: company && r.status === 'connected' ? company.id : `k-${r.id}`, ...fromServer(r.company) } : undefined,
    forCompany: r.for_company || undefined,
  }))
  notify()
}

// 키로 연결한 회사가 없어도 서버에 사이트 연결(판매 서버 + 회사 설정)이 있으면 그 회사로 본다(#1357)
let siteCompany: ConnectedCompany | null = null
function loadSiteCompany(): Promise<void> {
  const turn = turns.current()
  return companyConnection.get()
    .then((s) => {
      if (!turns.isCurrent(turn)) return // 세션이 바뀐 뒤 온 앞 사람 응답
      const on = s.source === 'connection' && s.connection?.enabled !== false && s.tenant
      siteCompany = on && s.tenant ? { id: s.tenant.id, name: s.tenant.name, industry: '', network: '', connectedAt: s.connection?.updated_at ?? undefined } : null
      notify()
    })
    .catch((e: unknown) => {
      // 토큰이 없거나 받지 않으면(로그인 전·로그아웃) 지난 사람이 읽은 값을 지운다. 연결 실패면 지난 값 그대로
      // (같은 사람의 값이다. 세션이 바뀌면 onSessionChange가 먼저 비운다)
      if (turns.isCurrent(turn) && refusedForThisPerson(e)) { siteCompany = null; notify() }
    })
}

// 로그인 전 화면용 공개 정보: 회사 id·이름·사이트 주소만 (dashboard-api `/auth/company`, 토큰 없이 열린 경로)
let publicCompany: ConnectedCompany | null = null
function loadPublicCompany(): Promise<void> {
  return fetch(`${DASHBOARD_API_URL}/api/dashboard/auth/company`, { cache: 'no-store' })
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
    .then((body: unknown) => {
      const c = readPublicCompany(body)
      publicCompany = c ? { id: c.id, name: c.name, industry: '', network: '', ...(c.siteUrl ? { siteUrl: c.siteUrl } : {}) } : null
      notify()
    })
    .catch(() => { /* 옛 서버(경로 없음)·연결 실패면 지난 값 그대로 */ })
}

/** 서버에서 키 목록·연결 회사·사이트 연결·공개 회사 정보를 다시 읽는다 */
// 처음 읽기가 끝났는지. 끝나기 전에는 "연결 전"으로 보고 회사 연결 화면으로 보내지 않는다(App의 Guarded)
let loaded = false
export function refreshCompany(): Promise<void> {
  const turn = turns.current()
  const keysNow = call<ServerState>('GET', PATH).then((s) => { if (turns.isCurrent(turn)) apply(s) }).catch((e: unknown) => {
    if (!turns.isCurrent(turn)) return // 세션이 바뀐 뒤 온 앞 사람 응답
    // 거절(401·403)이면 이 사람 값이 없다: 비우고 로그인 전 공개 정보를 쓴다.
    // 연결 실패면 지난 값 그대로(같은 사람의 값. 세션이 바뀐 직후라면 이미 비어 있고 계속 기다린다)
    if (refusedForThisPerson(e)) { keys = []; company = null; keysRead = false; waiting = false; notify() }
  })
  return Promise.all([keysNow, loadSiteCompany(), loadPublicCompany()]).then(() => {
    if (!loaded) { loaded = true; notify() }
  })
}
/** 회사 연결 저장·해제 뒤 (SiteConnect) */
export const refreshServerCompany = refreshCompany
try { void refreshCompany() } catch { loaded = true /* fetch가 없는 환경 */ }
// 로그인·로그아웃하면(세션이 바뀌면) 앞 사람 값을 바로 비우고 그 사람 토큰으로 다시 읽는다.
// 비우지 않으면 새 응답이 오기 전·연결 실패 때 앞 사람 회사 정보가 보인다(사후 리뷰 11/23).
// 아직 회사를 모르면 다 읽을 때까지 Guarded가 기다린다
onSessionChange(() => {
  turns.next()
  keys = []
  company = null
  siteCompany = null
  keysRead = false
  waiting = !!getSession()
  if (getSession() && !getCompany()) loaded = false
  notify()
  try { void refreshCompany() } catch { loaded = true; notify() }
})

export const getCompany = () => pickCompany(company, siteCompany, publicFallbackOk(keysRead, waiting) ? publicCompany : null)
export const useCompany = () => useSyncExternalStore(subscribe, getCompany)
/** 서버에서 연결 회사를 처음 한 번 다 읽었나 */
export const useCompanyLoaded = () => useSyncExternalStore(subscribe, () => loaded)
export const useKeys = () => useSyncExternalStore(subscribe, () => keys)

// ---------------------------------------------------------------- AXcellent 운영팀: 키 발급·폐기
/** 새 키 발급. 돌려받은 `key`가 전체 키(지금만 보인다) */
export async function issueKey(memo: string, newCompany?: NewCompany): Promise<IssuedKey> {
  const body: Record<string, string> = { memo }
  if (newCompany?.companyName.trim()) {
    body.company_name = newCompany.companyName.trim()
    body.industry = newCompany.industry
    if (newCompany.siteUrl?.trim()) body.site_url = newCompany.siteUrl.trim()
    if (newCompany.companyId?.trim()) body.company_id = newCompany.companyId.trim()
  }
  const s = await call<ServerState & { id: string; key: string }>('POST', `${PATH}/issue`, body)
  apply(s)
  const row = keys.find((k) => k.id === s.id)
  return { ...(row ?? { id: s.id, issuedAt: new Date().toISOString(), status: 'issued' as const }), key: s.key }
}
/** 폐기한 키로는 연결할 수 없다. 이미 그 키로 연결돼 있으면 연결도 끊는다 */
export async function revokeKey(id: string) {
  apply(await call<ServerState>('POST', `${PATH}/revoke`, { id }))
}

// ---------------------------------------------------------------- 고객사 시스템 관리자: 키 확인 → 회사 정보 입력 → 연결
export type KeyCheck = { ok: true; issued: IssuedKey } | { ok: false; reason: string }
type ServerCheck = { ok: boolean; reason: string | null; company: (Partial<ServerInfo> & { name: string }) | null; company_id?: string; signup?: boolean }
const issuedFrom = (key: string, r: ServerCheck): IssuedKey => ({
  id: '', key, issuedAt: '', status: 'issued',
  company: r.company ? { id: r.company_id ?? '', name: r.company.name, industry: r.company.industry ?? '', network: r.company.network ?? '', siteUrl: r.company.site_url || undefined } : undefined,
  forCompany: r.company_id || undefined,
  signup: r.signup,
})
/** 키 확인. 로그인했으면 그 회사 기준, 로그인 전(`publicCheck`)이면 회사 전용 키의 가입 가능 여부만 */
export async function checkKey(input: string, publicCheck = false): Promise<KeyCheck> {
  const key = input.trim().toUpperCase()
  const r = await call<ServerCheck>('POST', `${PATH}/${publicCheck ? 'check-public' : 'check'}`, { key })
  if (!r.ok) return { ok: false, reason: r.reason ?? '쓸 수 없는 키예요.' }
  return { ok: true, issued: issuedFrom(key, r) }
}
/** 회사 전용 키로 그 회사의 첫 관리자 가입(로그인 전). 성공하면 로그인 응답(세션)을 돌려준다 */
export async function signupWithKey(key: string, admin: { name: string; email: string; password: string }): Promise<unknown> {
  return call<unknown>('POST', `${PATH}/signup`, { key: key.trim().toUpperCase(), ...admin })
}
/** 지금 회사 연결을 끊은 관리자가 다른 회사 전용 키로 회사를 옮긴다(나와 내가 연결한 일반회원이 같이 간다).
 *  돌려받은 로그인 응답으로 세션을 새 회사로 바꾼다 */
export async function moveCompany(key: string): Promise<{ login: unknown; moved: number; from: string }> {
  const r = await call<{ moved_accounts: number; from_company: string } & Record<string, unknown>>('POST', `${PATH}/move`, { key: key.trim().toUpperCase() })
  return { login: r, moved: r.moved_accounts, from: r.from_company }
}
export async function connectWithKey(k: IssuedKey, info: CompanyInfo) {
  apply(await call<ServerState>('POST', `${PATH}/connect`, { key: k.key, ...toServer(info) }))
}
/** 연결된 회사 정보 고치기 (연결은 그대로) */
export async function updateCompanyInfo(info: CompanyInfo) {
  apply(await call<ServerState>('POST', `${PATH}/company`, toServer(info)))
}
/** 끊으면 서버가 그 키 줄의 회사·회사 정보를 지운다(연결 전 키로 돌아가 운영자만 본다, 사후 리뷰 11/23) */
export async function disconnect() {
  apply(await call<ServerState>('POST', `${PATH}/disconnect`))
}
