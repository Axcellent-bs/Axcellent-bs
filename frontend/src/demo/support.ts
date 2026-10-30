// 운영자 지원 모드: AXcellent 운영자가 고객사 화면(/system, 서비스 화면)에 보기 전용으로 들어간다.
// 고객사 계정을 따로 만들지 않고 관제 센터 고객사 목록에서 "들어가기"로 들어가며, 들어간 동안 화면 위에 띠가 뜬다.
// 데모에서는 이 브라우저에 연결된 회사(company.ts)에만 들어갈 수 있다. 브라우저에만 기억한다.
// 다음 단계(아직 없음): 접속 기록을 남겨 고객사에 보여 주기, 서버에서 운영자 토큰을 읽기 전용으로 막기.
import { useSyncExternalStore } from 'react'
import type { ConnectedCompany } from './company'

export type Support = { companyId: string; companyName: string; since: string }

const KEY = 'axcellent-demo-support'
const read = (): Support | null => {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    return raw && typeof raw.companyId === 'string' && typeof raw.companyName === 'string' ? raw : null
  } catch {
    return null
  }
}
let value = read()
const listeners = new Set<() => void>()
const write = (next: Support | null) => {
  value = next
  try {
    if (next) localStorage.setItem(KEY, JSON.stringify(next))
    else localStorage.removeItem(KEY)
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 지원 모드가 풀린다 */
  }
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}

export const getSupport = () => value
export const useSupport = () => useSyncExternalStore(subscribe, getSupport)
export const startSupport = (c: ConnectedCompany) => write({ companyId: c.id, companyName: c.name, since: new Date().toISOString() })
export const endSupport = () => write(null)

/** 지원 중인 회사가 지금 이 데모에 연결된 회사와 같은가 (연결이 끊기거나 바뀌면 지원 모드도 효력이 없다) */
export const supportActive = (s: Support | null, company: ConnectedCompany | null) => !!s && !!company && s.companyId === company.id
