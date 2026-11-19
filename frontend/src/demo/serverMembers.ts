// 서버 로그인일 때 회사 계정 목록 (dashboard-api `GET /auth/accounts`). 브라우저에 따로 기억하지 않고 서버 목록만 쓴다.
// 고객사 관리자는 고객사 계정(admin·member)만, 운영자는 모든 계정을 받는다. 일반회원은 목록을 못 읽는다(403)
// 화면 여러 곳(회원 표·PC 쓰는 사람·요청 승인자)이 같은 목록을 보도록 한 군데에서 읽고 알린다.
import { useEffect, useSyncExternalStore } from 'react'
import { listAccounts } from '@/lib/authApi'
import type { ServerAccount } from '@/lib/humanSession'

export type ServerMembers = {
  /** 서버에서 받은 계정. 아직 못 받았으면 null */
  rows: ServerAccount[] | null
  /** 마지막으로 읽다 난 문제 */
  error: string | null
  loading: boolean
}

let state: ServerMembers = { rows: null, error: null, loading: false }
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()
const set = (next: Partial<ServerMembers>) => {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}
export const getServerMembers = () => state

/** 서버 목록을 다시 읽는다. 읽는 중이면 그 결과를 같이 기다린다 */
export function reloadServerMembers(): Promise<void> {
  if (inflight) return inflight
  set({ loading: true })
  inflight = listAccounts()
    .then((rows) => set({ rows, error: null, loading: false }))
    .catch((x: unknown) => set({ error: x instanceof Error ? x.message : '계정 목록을 읽지 못했어요.', loading: false }))
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** 로그아웃·다른 계정으로 들어갈 때: 앞 사람의 목록을 남기지 않는다 */
export function clearServerMembers() {
  set({ rows: null, error: null, loading: false })
}

/** enabled면(서버 로그인 + 목록을 읽을 수 있는 역할) 처음 한 번 읽는다 */
export function useServerMembers(enabled: boolean): ServerMembers {
  const s = useSyncExternalStore(subscribe, getServerMembers)
  useEffect(() => {
    if (enabled && getServerMembers().rows === null && !inflight) void reloadServerMembers()
  }, [enabled])
  return s
}
