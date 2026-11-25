// 기획·개발 화면 상태 SSE(`GET /api/planning/events`)를 로그인 세션 회사로 받는다 (11/25 회사 분리).
// - EventSource는 머리(Authorization)를 실을 수 없어 서버가 회사를 몰랐다 → 누비투어 화면에 캣북스 상태가 보였다
// - 로그인 세션이 있으면 fetch 스트림으로 세션 토큰을 실어 받고, 없으면 예전처럼 EventSource
// - 끊기면 3초 뒤 다시 잇는다. close()로 멈춘다
// import.meta.env를 읽지 않아 node --test로 그대로 돈다.

export interface SseEvent { event: string; data: string }

/** SSE 글을 받은 만큼 나눈다. 끝나지 않은 마지막 조각은 rest로 돌려준다 */
export function splitSse(buffer: string): { events: SseEvent[]; rest: string } {
  const text = buffer.replace(/\r\n/g, '\n')
  const blocks = text.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: SseEvent[] = []
  for (const block of blocks) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue // 주석(keepalive)
      const i = line.indexOf(':')
      const field = i < 0 ? line : line.slice(0, i)
      const value = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length) events.push({ event, data: data.join('\n') })
  }
  return { events, rest }
}

export interface StreamHandlers {
  onState: (data: string) => void
  onOpen: () => void
  onError: (open: boolean) => void
}

export interface StreamDeps {
  fetch?: typeof fetch
  EventSource?: typeof EventSource
  retryMs?: number
}

/** 상태 스트림을 연다. `authorization`이 있으면 fetch 스트림(머리 실음), 없으면 EventSource. 돌려준 함수로 닫는다 */
export function openStateStream(url: string, authorization: string | null, h: StreamHandlers, deps: StreamDeps = {}): () => void {
  if (!authorization) {
    const ES = deps.EventSource ?? globalThis.EventSource
    const es = new ES(url)
    es.addEventListener('state', (e) => h.onState((e as MessageEvent).data as string))
    es.onopen = () => h.onOpen()
    es.onerror = () => h.onError(es.readyState === ES.OPEN)
    return () => es.close()
  }
  const doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis)
  const retryMs = deps.retryMs ?? 3000
  let closed = false
  let ctrl: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  const again = () => {
    if (closed) return
    h.onError(false)
    timer = setTimeout(() => void run(), retryMs)
  }
  const run = async () => {
    if (closed) return
    ctrl = new AbortController()
    try {
      const res = await doFetch(url, { headers: { Accept: 'text/event-stream', Authorization: authorization }, cache: 'no-store', signal: ctrl.signal })
      if (!res.ok || !res.body) { again(); return }
      h.onOpen()
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const { events, rest } = splitSse(buffer)
        buffer = rest
        for (const ev of events) if (ev.event === 'state') h.onState(ev.data)
      }
      again()
    } catch {
      again()
    }
  }
  void run()
  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    ctrl?.abort()
  }
}
