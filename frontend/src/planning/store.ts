// 기획개발 화면 상태는 기획개발 백엔드(planning 에이전트) 한 곳에서 온다.
// SSE `GET /api/planning/events`로 바뀔 때마다 전체 상태를 받고, 사람 버튼은 `POST /api/planning/actions`로 보낸다.
// 화면 안에서 상태를 지어내거나 혼자 진행시키지 않는다. 서버가 꺼져 있으면 빈 화면과 "연결 안 됨"을 보여 준다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { EMPTY_STATE, PlanningApiError, fromBackend, planningApi, type QueueItem } from './api'
import type { Action, State } from './data'

/** 액션이 실패하면 화면(PlanningPage)이 토스트로 알린다 */
export const ACTION_ERROR_EVENT = 'planning:action-error'
const report = (message: string) => window.dispatchEvent(new CustomEvent(ACTION_ERROR_EVENT, { detail: message }))

export function useSharedStore(): [State, (a: Action) => void, boolean] {
  const [state, setState] = useState<State>(EMPTY_STATE)
  const [connected, setConnected] = useState(false)
  const reviews = useRef(state.reviews)
  reviews.current = state.reviews

  useEffect(() => {
    let alive = true
    const apply = async (raw: Record<string, unknown>) => {
      const queue = await planningApi.reviewQueue().catch(() => [] as QueueItem[])
      if (alive) setState(fromBackend(raw, queue))
    }
    const es = new EventSource(planningApi.eventsUrl())
    es.addEventListener('state', (e) => {
      setConnected(true)
      void apply(JSON.parse((e as MessageEvent).data) as Record<string, unknown>)
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(es.readyState === EventSource.OPEN)
    return () => {
      alive = false
      es.close()
    }
  }, [])

  const dispatch = useCallback((a: Action) => {
    let send: Action | undefined = a
    // 교차검증 대기열의 "승인"은 백엔드가 알려 준 확정·머지 액션(decide_with)으로 보낸다
    if (a.type === 'decideReview' || (a.type === 'humanDecide' && a.ok)) {
      const r = reviews.current.find((x) => x.id === a.review) as { decideWith?: Action } | undefined
      send = r?.decideWith
      if (!send) return report('이 검증 건을 처리할 서버 액션이 없습니다')
    } else if (a.type === 'humanDecide') {
      return report('보류는 아직 서버에서 받지 않습니다. 기다리면 대기열에 남습니다')
    }
    planningApi.action(send).catch((e: unknown) => {
      if (e instanceof PlanningApiError && e.code === 'UNKNOWN_ACTION') report('이 기능은 아직 기획개발 서버에 연결되지 않았습니다')
      else if (e instanceof PlanningApiError) report(e.message)
      else report('기획개발 서버에 연결할 수 없습니다')
    })
  }, [])

  return [state, dispatch, connected]
}
