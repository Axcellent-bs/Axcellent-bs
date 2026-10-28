// 이 AXcellent에 연결된 고객사와 AXcellent가 발급한 연결 키. 데모라 브라우저에만 기억한다.
// - 키 발급: AXcellent 운영팀이 관제 센터(/control/keys)에서 한다.
// - 연결: 고객사 시스템 관리자가 받은 키를 넣어서 한다(/system/connect). 연결을 끊는 것도 고객사가 한다.
// 상단바의 "AXcellent with <회사>"와 로그인 화면이 연결된 회사를 따른다. 끊으면 null(회사 이름 없음).
import { useSyncExternalStore } from 'react'

export type ConnectedCompany = { id: string; name: string; industry: string; network: string }
export type KeyStatus = 'issued' | 'connected' | 'revoked'
export type IssuedKey = { key: string; company: ConnectedCompany; issuedAt: string; status: KeyStatus }

const CATBOOKS: ConnectedCompany = { id: 'catbooks', name: '캣북스', industry: '온라인 서점', network: '192.168.10.0/24' }
const DEFAULT_KEYS: IssuedKey[] = [
  { key: 'AXC-K3TB-8W2N-C4T1', company: CATBOOKS, issuedAt: '2026-10-20T09:00:00+09:00', status: 'connected' },
  { key: 'AXC-7Q2M-91KD-4F8A', company: { id: 'saebyeok', name: '새벽커피', industry: '카페 체인', network: '192.168.30.0/24' }, issuedAt: '2026-10-28T10:40:00+09:00', status: 'issued' },
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
const keyStore = store<IssuedKey[]>('axcellent-demo-keys', DEFAULT_KEYS)

export const getCompany = companyStore.get
export const useCompany = () => useSyncExternalStore(companyStore.subscribe, companyStore.get)
export const useKeys = () => useSyncExternalStore(keyStore.subscribe, keyStore.get)

const setStatus = (key: string, status: KeyStatus) => keyStore.set(keyStore.get().map((k) => (k.key === key ? { ...k, status } : k)))

// ---------------------------------------------------------------- AXcellent 운영팀: 키 발급·폐기
const part = () => Array.from({ length: 4 }, () => 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 32)]).join('')
export function issueKey(name: string, industry: string): IssuedKey {
  const k: IssuedKey = {
    key: `AXC-${part()}-${part()}-${part()}`,
    company: { id: `c-${part().toLowerCase()}`, name, industry, network: '192.168.40.0/24' },
    issuedAt: new Date().toISOString(),
    status: 'issued',
  }
  keyStore.set([k, ...keyStore.get()])
  return k
}
/** 폐기한 키로는 연결할 수 없다. 이미 그 키로 연결돼 있으면 연결도 끊는다 */
export function revokeKey(key: string) {
  const k = keyStore.get().find((x) => x.key === key)
  if (k && companyStore.get()?.id === k.company.id) companyStore.set(null)
  setStatus(key, 'revoked')
}

// ---------------------------------------------------------------- 고객사 시스템 관리자: 키로 연결·끊기
export type KeyCheck = { ok: true; issued: IssuedKey } | { ok: false; reason: string }
export function checkKey(input: string): KeyCheck {
  const key = input.trim().toUpperCase()
  const k = keyStore.get().find((x) => x.key === key)
  if (!k) return { ok: false, reason: 'AXcellent에서 발급한 키가 아니에요. 키를 다시 확인해 주세요.' }
  if (k.status === 'revoked') return { ok: false, reason: '폐기된 키예요. AXcellent에 새 키를 요청해 주세요.' }
  return { ok: true, issued: k }
}
export function connectWithKey(k: IssuedKey) {
  // 다른 회사로 바꿔 연결하면 이전 회사의 키는 다시 쓸 수 있는 상태로 돌린다
  const prev = companyStore.get()
  const keys = keyStore.get().map((x) =>
    x.key === k.key ? { ...x, status: 'connected' as const } : prev && x.company.id === prev.id && x.status === 'connected' ? { ...x, status: 'issued' as const } : x,
  )
  keyStore.set(keys)
  companyStore.set(k.company)
}
export function disconnect() {
  const prev = companyStore.get()
  if (prev) keyStore.set(keyStore.get().map((x) => (x.company.id === prev.id && x.status === 'connected' ? { ...x, status: 'issued' } : x)))
  companyStore.set(null)
}
