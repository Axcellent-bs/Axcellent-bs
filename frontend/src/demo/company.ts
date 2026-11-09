// 이 AXcellent에 연결된 고객사와 AXcellent가 발급한 연결 키. 데모라 브라우저에만 기억한다.
// - 키 발급: AXcellent 운영팀이 관제 센터(/control/keys)에서 한다. 키에는 회사 정보가 없고 "연결해도 된다"는 권한만 있다.
// - 연결: 고객사 시스템 관리자가 키를 넣고(확인), 회사 정보를 직접 입력해 연결한다(/system/connect). 끊는 것도 고객사가 한다.
// 상단바의 "AXcellent with <회사>"와 로그인 화면이 연결된 회사를 따른다. 끊으면 null(회사 이름 없음).
import { useSyncExternalStore } from 'react'

export type ConnectedCompany = {
  id: string
  name: string
  industry: string
  network: string
  /** 연결에 쓴 키와 연결한 시각 (AXcellent 연결 화면에 보여 준다) */
  key?: string
  connectedAt?: string
}
export type CompanyInfo = Pick<ConnectedCompany, 'name' | 'industry' | 'network'>
export type KeyStatus = 'issued' | 'connected' | 'revoked'
export type IssuedKey = {
  key: string
  /** 운영팀 메모(누구에게 보냈는지). 키에는 들어가지 않는다 */
  memo?: string
  issuedAt: string
  status: KeyStatus
  /** 고객사가 연결하면서 입력한 회사 정보 (연결 전에는 없음) */
  company?: ConnectedCompany
}

const CATBOOKS: ConnectedCompany = { id: 'catbooks', name: '캣북스', industry: '온라인 쇼핑몰', network: '192.168.10.0/24', key: 'AXC-K3TB-8W2N-C4T1', connectedAt: '2026-10-20T09:30:00+09:00' }
const DEFAULT_KEYS: IssuedKey[] = [
  { key: 'AXC-K3TB-8W2N-C4T1', memo: '캣북스 IT 담당', issuedAt: '2026-10-20T09:00:00+09:00', status: 'connected', company: CATBOOKS },
  { key: 'AXC-7Q2M-91KD-4F8A', memo: '카페 체인 도입 문의', issuedAt: '2026-10-28T10:40:00+09:00', status: 'issued' },
]

// ---------------------------------------------------------------- 작은 저장소 (화면 여러 곳이 같은 값을 본다)
function store<T>(key: string, fallback: T) {
  let value: T = (() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? fallback : (JSON.parse(raw) as T)
    } catch {
      return fallback
    }
  })()
  const listeners = new Set<() => void>()
  return {
    get: () => value,
    set(next: T) {
      value = next
      try {
        localStorage.setItem(key, JSON.stringify(next))
      } catch {
        /* 저장이 막힌 브라우저는 새로고침하면 처음 상태로 돌아간다 */
      }
      listeners.forEach((l) => l())
    },
    subscribe(l: () => void) {
      listeners.add(l)
      return () => listeners.delete(l)
    },
  }
}

const companyStore = store<ConnectedCompany | null>('axcellent-demo-company-v2', CATBOOKS)
const keyStore = store<IssuedKey[]>('axcellent-demo-keys-v2', DEFAULT_KEYS)

export const getCompany = companyStore.get
export const useCompany = () => useSyncExternalStore(companyStore.subscribe, companyStore.get)
export const useKeys = () => useSyncExternalStore(keyStore.subscribe, keyStore.get)

const setStatus = (key: string, status: KeyStatus) => keyStore.set(keyStore.get().map((k) => (k.key === key ? { ...k, status } : k)))

// ---------------------------------------------------------------- AXcellent 운영팀: 키 발급·폐기
const part = () => Array.from({ length: 4 }, () => 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 32)]).join('')
export function issueKey(memo: string): IssuedKey {
  const k: IssuedKey = { key: `AXC-${part()}-${part()}-${part()}`, memo: memo || undefined, issuedAt: new Date().toISOString(), status: 'issued' }
  keyStore.set([k, ...keyStore.get()])
  return k
}
/** 폐기한 키로는 연결할 수 없다. 이미 그 키로 연결돼 있으면 연결도 끊는다 */
export function revokeKey(key: string) {
  const k = keyStore.get().find((x) => x.key === key)
  if (k?.company && companyStore.get()?.id === k.company.id) companyStore.set(null)
  setStatus(key, 'revoked')
}

// ---------------------------------------------------------------- 고객사 시스템 관리자: 키 확인 → 회사 정보 입력 → 연결
export type KeyCheck = { ok: true; issued: IssuedKey } | { ok: false; reason: string }
export function checkKey(input: string): KeyCheck {
  const key = input.trim().toUpperCase()
  const k = keyStore.get().find((x) => x.key === key)
  if (!k) return { ok: false, reason: 'AXcellent에서 발급한 키가 아니에요. 키를 다시 확인해 주세요.' }
  if (k.status === 'revoked') return { ok: false, reason: '폐기된 키예요. AXcellent에 새 키를 요청해 주세요.' }
  if (k.status === 'connected' && k.company?.id !== companyStore.get()?.id) return { ok: false, reason: '이미 다른 회사가 쓰고 있는 키예요.' }
  return { ok: true, issued: k }
}
export function connectWithKey(k: IssuedKey, info: CompanyInfo) {
  const company: ConnectedCompany = { id: k.company?.id ?? `c-${k.key.slice(4, 8).toLowerCase()}`, ...info, key: k.key, connectedAt: new Date().toISOString() }
  // 다른 키로 바꿔 연결하면 이전 키는 다시 쓸 수 있는 상태로 돌린다
  const prev = companyStore.get()
  keyStore.set(keyStore.get().map((x) =>
    x.key === k.key ? { ...x, status: 'connected' as const, company }
      : prev && x.company?.id === prev.id && x.status === 'connected' ? { ...x, status: 'issued' as const } : x,
  ))
  companyStore.set(company)
}
/** 연결된 회사 정보 고치기 (연결은 그대로) */
export function updateCompanyInfo(info: CompanyInfo) {
  const cur = companyStore.get()
  if (!cur) return
  const next = { ...cur, ...info }
  companyStore.set(next)
  keyStore.set(keyStore.get().map((x) => (x.company?.id === cur.id ? { ...x, company: next } : x)))
}
/** 끊어도 키에 남긴 회사 정보는 두어서, 같은 키로 다시 연결할 때 미리 채운다 */
export function disconnect() {
  const prev = companyStore.get()
  if (prev) keyStore.set(keyStore.get().map((x) => (x.company?.id === prev.id && x.status === 'connected' ? { ...x, status: 'issued' } : x)))
  companyStore.set(null)
}

// 시연: 주소에 ?company=none 을 붙여 열면 회사 연결 전 상태로 시작한다
try {
  if (new URLSearchParams(window.location.search).get('company') === 'none') disconnect()
} catch {
  /* 주소를 못 읽는 환경은 그대로 */
}
