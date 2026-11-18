// 이 AXcellent에 연결된 고객사와 AXcellent가 발급한 연결 키. 서버(dashboard-api `connection_keys.py`)에 저장해
// 어느 PC에서든 같은 키 목록·연결 회사가 보인다(예전에는 브라우저에만 있어서 다른 PC는 비어 있었다).
// - 키 발급: AXcellent 운영팀이 관제 센터(/control/keys)에서 한다. 키에는 회사 정보가 없고 "연결해도 된다"는 권한만 있다.
//   전체 키는 발급할 때 한 번만 받는다. 목록에는 가린 키만 온다.
// - 연결: 고객사 시스템 관리자가 키를 넣고(서버가 확인), 회사 정보를 입력해 연결한다(/system/connect). 끊는 것도 고객사가 한다.
// 상단바의 "AXcellent with <회사>"와 로그인 화면이 연결된 회사를 따른다. 끊으면 null(회사 이름 없음).
import { useSyncExternalStore } from 'react'
import { call } from '@/lib/companyConnection'

export type ConnectedCompany = {
  id: string
  name: string
  industry: string
  network: string
  /** 연결에 쓴 키(가린 값)와 연결한 시각 */
  key?: string
  connectedAt?: string
}
export type CompanyInfo = Pick<ConnectedCompany, 'name' | 'industry' | 'network'>
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
}

type ServerRow = { id: string; key: string; memo: string | null; issued_at: string; status: KeyStatus; company: CompanyInfo | null }
type ServerCompany = CompanyInfo & { id: string; key: string | null; connected_at: string }
type ServerState = { keys: ServerRow[]; company: ServerCompany | null; editable: boolean }

const PATH = '/api/dashboard/connection-keys'
let keys: IssuedKey[] = []
let company: ConnectedCompany | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

function apply(s: ServerState) {
  company = s.company
    ? { id: s.company.id, name: s.company.name, industry: s.company.industry, network: s.company.network, key: s.company.key ?? undefined, connectedAt: s.company.connected_at }
    : null
  keys = s.keys.map((r) => ({
    id: r.id,
    key: r.key,
    memo: r.memo ?? undefined,
    issuedAt: r.issued_at,
    status: r.status,
    company: r.company ? { id: company && r.status === 'connected' ? company.id : `k-${r.id}`, ...r.company } : undefined,
  }))
  notify()
}

/** 서버에서 키 목록·연결 회사를 다시 읽는다 */
export function refreshCompany(): Promise<void> {
  return call<ServerState>('GET', PATH).then(apply).catch(() => { /* 서버를 못 읽으면 지난 값 그대로 */ })
}
try { void refreshCompany() } catch { /* fetch가 없는 환경 */ }

export const getCompany = () => company
export const useCompany = () => useSyncExternalStore(subscribe, getCompany)
export const useKeys = () => useSyncExternalStore(subscribe, () => keys)

// ---------------------------------------------------------------- AXcellent 운영팀: 키 발급·폐기
/** 새 키 발급. 돌려받은 `key`가 전체 키(지금만 보인다) */
export async function issueKey(memo: string): Promise<IssuedKey> {
  const s = await call<ServerState & { id: string; key: string }>('POST', `${PATH}/issue`, { memo })
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
export async function checkKey(input: string): Promise<KeyCheck> {
  const key = input.trim().toUpperCase()
  const r = await call<{ ok: boolean; reason: string | null; company: CompanyInfo | null }>('POST', `${PATH}/check`, { key })
  if (!r.ok) return { ok: false, reason: r.reason ?? '쓸 수 없는 키예요.' }
  return { ok: true, issued: { id: '', key, issuedAt: '', status: 'issued', company: r.company ? { id: '', ...r.company } : undefined } }
}
export async function connectWithKey(k: IssuedKey, info: CompanyInfo) {
  apply(await call<ServerState>('POST', `${PATH}/connect`, { key: k.key, ...info }))
}
/** 연결된 회사 정보 고치기 (연결은 그대로) */
export async function updateCompanyInfo(info: CompanyInfo) {
  apply(await call<ServerState>('POST', `${PATH}/company`, info))
}
/** 끊어도 키에 남긴 회사 정보는 두어서, 같은 키로 다시 연결할 때 미리 채운다 */
export async function disconnect() {
  apply(await call<ServerState>('POST', `${PATH}/disconnect`))
}
