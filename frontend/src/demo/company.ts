// 지금 이 AXcellent가 연결된 고객사. 상단바의 "AXcellent with <회사>"와 로그인 화면이 이 값을 따른다.
// 관제 센터 고객사 탭에서 연결·연결 끊기를 하고, 고객사 연결 시연이 끝나면 새 회사로 바뀐다. 끊으면 null(회사 이름 없음).
// 데모라 브라우저에만 기억한다.
import { useSyncExternalStore } from 'react'
import { COMPANIES } from '@/control/data'

export type ConnectedCompany = { id: string; name: string; network: string }

/** 시연에서 새로 연결하는 회사 (control/ConnectDemo.tsx) */
export const DEMO_NEW_COMPANY: ConnectedCompany = { id: 'saebyeok', name: '새벽커피', network: '192.168.30.0/24' }

const KNOWN: ConnectedCompany[] = [...COMPANIES.map(({ id, name, network }) => ({ id, name, network })), DEMO_NEW_COMPANY]
const KEY = 'axcellent-demo-company'
const DEFAULT_ID = 'catbooks'

const read = (): ConnectedCompany | null => {
  try {
    const id = localStorage.getItem(KEY)
    if (id === '') return null // 연결 끊음
    return KNOWN.find((c) => c.id === (id ?? DEFAULT_ID)) ?? null
  } catch {
    return KNOWN.find((c) => c.id === DEFAULT_ID) ?? null
  }
}
let current = read()
const listeners = new Set<() => void>()

export const getCompany = () => current
export function connectCompany(id: string | null) {
  current = id ? KNOWN.find((c) => c.id === id) ?? null : null
  try {
    localStorage.setItem(KEY, current?.id ?? '')
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 캣북스로 돌아간다 */
  }
  listeners.forEach((l) => l())
}

export function useCompany() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    getCompany,
  )
}
