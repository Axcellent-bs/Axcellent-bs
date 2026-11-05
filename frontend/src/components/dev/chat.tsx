// AI와 대화: 개발 에이전트와 대화하다 필요한 것이 모이면 에이전트가 코딩 작업(→ PR)을 시작한다.
// 대화는 REST(/dev-chat)로 주고받고, SSE에 chats가 있으면 그걸로 갱신한다. 없으면 2초마다 다시 읽는다
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Chip, useToast, type ChipTone } from '@dejafix/common-frontend'
import type { ChatMessage, ChatStatus, ChatThread, CodingMode, CodingStatus, State as S, Task } from '@/lib/planningData'
import { DEV_API, PlanningApiError, chatApi, codingSettingsApi, useMe, vcsApi, type CodingSettings, type VcsConnection } from '@/lib/planning'
import { CODING_LABEL, CancelButton, ResumeButton, codingState, isRunning } from '@/components/dev/coding'
import { State } from '@/components/planning-common/parts'
import { go, toPath } from '@/components/planning-common/links'
import { Tag } from '@/components/planning-common/parts'

const api = chatApi(DEV_API)

const STATUS: Record<ChatStatus, { label: string; tone: ChipTone }> = {
  talking: { label: '대화 중', tone: 'neutral' },
  started: { label: '작업 시작', tone: 'green' },
  blocked: { label: '막힘', tone: 'amber' },
  waiting: { label: '앞 요청 대기', tone: 'amber' },
  answering: { label: '답하는 중', tone: 'neutral' },
  closed: { label: '끝난 대화', tone: 'neutral' },
}
const RUNNING: CodingStatus[] = ['queued', 'reading', 'writing', 'reviewing']
const EXAMPLES = [
  '고객센터 운영 시간 문구를 평일 9–18시로 바꿔 줘',
  '주문 목록에 배송 상태 필터를 추가해 줘',
  '로그인 실패 시 안내 문구를 친절하게 바꿔 줘',
]
const RECENT_MS = 10 * 60_000

const taskOf = (s: S, t: ChatThread): Task | undefined => (t.taskId ? s.tasks.find((x) => x.id === t.taskId) : undefined)

/** 작업이 아직 돌고 있는가. SSE에 작업이 아직 없으면 방금 시작한 것만 돈다고 본다 */
function running(s: S, t: ChatThread): boolean {
  if (t.status !== 'started') return false
  const c = taskOf(s, t)?.coding
  if (c) return RUNNING.includes(c.status)
  return Date.now() - new Date(t.updatedAt).getTime() < RECENT_MS
}

// 같은 대화는 더 최근 것(메시지 많은 것)을 쓴다
function merge(a: ChatThread[], b: ChatThread[]): ChatThread[] {
  const m = new Map<string, ChatThread>()
  for (const t of [...a, ...b]) {
    const o = m.get(t.id)
    if (!o || t.updatedAt > o.updatedAt || (t.updatedAt === o.updatedAt && t.messages.length > o.messages.length)) m.set(t.id, t)
  }
  return [...m.values()].sort((x, y) => (y.updatedAt > x.updatedAt ? 1 : y.updatedAt < x.updatedAt ? -1 : 0))
}

/** 헤더 칩과 패널이 같이 쓰는 대화 상태. 패널을 닫았다 열어도 남는다 */
export function useDevChat(s: S, on: boolean) {
  const [local, setLocal] = useState<ChatThread[]>([])
  const [curId, setCurId] = useState<string | null | undefined>(undefined) // undefined = 아직 안 고름(가장 최근 대화)
  const [draft, setDraft] = useState('')
  useEffect(() => {
    if (on) api.list().then(setLocal).catch(() => undefined)
  }, [on])
  const threads = merge(s.chats ?? [], local)
  const upsert = (t: ChatThread) => setLocal((prev) => [t, ...prev.filter((x) => x.id !== t.id)])
  const cur = curId === null ? undefined : threads.find((t) => t.id === (curId ?? threads[0]?.id))
  return {
    threads, cur, upsert, draft, setDraft,
    select: (id: string | null) => setCurId(id),
    live: !!s.chats,
    working: threads.filter((t) => running(s, t)).map((t) => t.id),
  }
}
export type DevChat = ReturnType<typeof useDevChat>

type Err = { code: string; message: string }

/** 오른쪽 대화 패널 */
const MODE_KEY = 'axcellent-dev-chat-mode'
const readMode = (): CodingMode => { try { return localStorage.getItem(MODE_KEY) === 'subscription' ? 'subscription' : 'agent' } catch { return 'agent' } }

export function ChatPanel({ s, chat, connected, onClose, onTools }: { s: S; chat: DevChat; connected: boolean; onClose: () => void; onTools: () => void }) {
  const toast = useToast()
  const { cur, threads, draft, setDraft } = chat
  const [conns, setConns] = useState<VcsConnection[]>([])
  const [conn, setConn] = useState('')
  const [pending, setPending] = useState<string | null>(null) // 보낸 글 (답 기다리는 중)
  const [pick, setPick] = useState<CodingMode>(readMode)
  const [err, setErr] = useState<Err | null>(null)
  const [setting, setSetting] = useState<CodingSettings | null>(null) // 본인 Claude 연결·서버 AI 체험 한도
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    codingSettingsApi(DEV_API).get().then(setSetting).catch(() => setSetting(null))
  }, [])
  useEffect(() => {
    vcsApi(DEV_API).list().then((c) => {
      setConns(c)
      setConn((x) => x || c[0]?.id || '')
    }).catch(() => setConns([]))
  }, [])
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  useEffect(() => setErr(null), [cur?.id])

  // SSE에 대화가 없을 때만 다시 읽는다
  const poll = !chat.live && !!cur && (!!pending || running(s, cur))
  useEffect(() => {
    if (!poll || !cur) return
    const id = cur.id
    const t = setInterval(() => api.get(id).then(chat.upsert).catch(() => undefined), 2000)
    return () => clearInterval(t)
  }, [poll, cur?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const msgs = cur?.messages ?? []
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight })
  }, [msgs.length, pending, err])

  const repo = cur?.connection ?? conn
  // 작업을 띄운 대화는 그 대화의 모드 그대로, 새 대화는 고른 모드 (이 브라우저에 기억)
  // 내 Claude는 이 화면을 쓰는 사람이 본인 Claude(MCP)를 연결했을 때만 고른다 (그 사람에게 맡긴다).
  // 나를 모르면(사람 토큰 없음) 예전처럼 회사에 연결한 개발자가 있는지로 본다
  const me = useMe()
  const noClaude = me?.name ? !me.claude_connected : !!setting && setting.agents_connected.length === 0
  const trialOut = !!setting && setting.trial_used >= setting.trial_limit
  const mode: CodingMode = cur?.taskId ? cur.mode ?? 'agent' : noClaude && pick === 'subscription' ? 'agent' : trialOut && !noClaude ? 'subscription' : pick
  const choose = (m: CodingMode) => {
    setPick(m)
    try { localStorage.setItem(MODE_KEY, m) } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ }
  }
  const send = async (text = draft) => {
    const message = text.trim()
    if (!message || pending) return
    setPending(message)
    setErr(null)
    setDraft('')
    try {
      const t = await api.send({ ...(cur ? { thread_id: cur.id } : {}), message, ...(repo ? { connection: repo } : {}), mode })
      chat.upsert(t)
      chat.select(t.id)
    } catch (x) {
      const code = x instanceof PlanningApiError ? x.code : 'NETWORK'
      const msg = code === 'LLM_NOT_CONFIGURED' ? 'LLM 설정 필요 (backend/.env)'
        : code === 'NO_VCS' ? '연결된 레포 없음'
        : x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요'
      setErr({ code, message: msg })
      setDraft(message)
      toast.show(msg)
    } finally {
      setPending(null)
      input.current?.focus()
    }
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }

  const st = cur ? STATUS[cur.status] : undefined
  return (
    <aside className="chat-drawer" role="dialog" aria-label="AI와 대화">
      <div className="chat-drawer-head">
        <strong>AI와 대화</strong>
        {st && <Chip tone={st.tone}>{st.label}</Chip>}
        <button type="button" className="btn-quiet px-2 py-1 ml-auto" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <div className="chat-drawer-bar">
        <select className="text-input chat-sel" value={cur?.id ?? ''} onChange={(e) => chat.select(e.target.value || null)} aria-label="대화 고르기" disabled={!!pending}>
          <option value="">새 대화</option>
          {threads.map((t) => <option key={t.id} value={t.id}>{`${t.title || t.id} · ${STATUS[t.status]?.label ?? t.status}`}</option>)}
        </select>
        <button type="button" className="btn-ghost" onClick={() => chat.select(null)} disabled={!!pending || !cur}>새 대화</button>
      </div>
      {conns.length > 1 && (
        <div className="chat-drawer-bar">
          <span className="muted small">레포</span>
          <select className="text-input chat-sel mono" value={repo} onChange={(e) => setConn(e.target.value)} aria-label="대상 레포" disabled={!!pending || !!cur?.connection}>
            {conns.map((c) => <option key={c.id} value={c.id}>{c.repo}</option>)}
          </select>
        </div>
      )}

      <div className="chat-drawer-msgs" ref={box} aria-live="polite">
        {!msgs.length && !pending && (
          <div className="chat-examples">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" className="q-chip" onClick={() => { setDraft(x); input.current?.focus() }}>{x}</button>
            ))}
          </div>
        )}
        {msgs.map((m) => <Msg key={m.id} m={m} s={s} onOpen={onClose} />)}
        {pending && msgs[msgs.length - 1]?.text !== pending && <div className="bubble user">{pending}</div>}
        {pending && <div className="bubble ai"><span className="typing" aria-label="답 쓰는 중"><i /><i /><i /></span></div>}
        {err && (
          <div className="bubble ai err">
            {err.message}
            {err.code === 'NO_VCS' && <Tools onTools={onTools} />}
          </div>
        )}
        {!pending && !err && cur?.status === 'blocked' && <Tools onTools={onTools} />}
      </div>

      <div className="sl-composer">
        <textarea ref={input} rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
          placeholder={cur ? '답장' : '무엇을 바꿀까요'} aria-label="메시지" disabled={!!pending || !connected} />
        <div className="sl-composer-bar">
          <div className="seg-toggle" role="group" aria-label="누가 코딩하나" title={cur?.taskId ? '작업을 띄운 대화는 바꿀 수 없어요' : '서버 AI는 회사 토큰, 내 Claude는 내 구독으로 코딩해요'}>
            <button type="button" aria-pressed={mode === 'agent'} onClick={() => choose('agent')} disabled={!!pending || !!cur?.taskId || (trialOut && !noClaude)} title={trialOut ? `서버 AI 체험 한도 다 씀 (${setting!.trial_used.toLocaleString()}/${setting!.trial_limit.toLocaleString()} 토큰)` : undefined}>서버 AI</button>
            <button type="button" aria-pressed={mode === 'subscription'} onClick={() => choose('subscription')} disabled={!!pending || !!cur?.taskId || noClaude} title={noClaude ? '연결된 본인 Claude 없음 (개발 도구 연결 → MCP)' : undefined}>내 Claude</button>
          </div>
          <button type="button" className="send" onClick={() => void send()} disabled={!!pending || !connected || !draft.trim()} aria-label="보내기">↑</button>
        </div>
      </div>
    </aside>
  )
}

const Tools = ({ onTools }: { onTools: () => void }) => (
  <div><button type="button" className="btn-ghost" onClick={onTools}>개발 도구 연결</button></div>
)

function Msg({ m, s, onOpen }: { m: ChatMessage; s: S; onOpen: () => void }) {
  if (m.role === 'user') return <div className="bubble user">{m.text}</div>
  if (m.kind === 'error') return <div className="bubble ai err">{m.text}</div>
  if (m.kind === 'question' || m.kind === 'waiting') return <div className="bubble ai q">{m.text}</div>
  if (m.kind === 'plan') {
    const p = m.plan
    return (
      <>
        {m.text && <div className="bubble ai">{m.text}</div>}
        {p && (
          <div className="chat-card">
            <div className="chat-card-h">{p.title}</div>
            {!!p.done.length && <ul>{p.done.map((d, i) => <li key={i}>{d}</li>)}</ul>}
            {!!p.paths.length && <div className="chat-paths">{p.paths.map((x) => <span key={x} className="point">{x}</span>)}</div>}
          </div>
        )}
      </>
    )
  }
  if (m.kind === 'started') return <Started m={m} s={s} onOpen={onOpen} />
  return <div className="bubble ai">{m.text}</div>
}

function Started({ m, s, onOpen }: { m: ChatMessage; s: S; onOpen: () => void }) {
  const id = m.taskId
  const c = id ? s.tasks.find((t) => t.id === id)?.coding : undefined
  const status: CodingStatus = c?.status ?? 'queued'
  const files = c?.files?.length ?? 0
  return (
    <>
      {m.text && <div className="bubble ai">{m.text}</div>}
      <div className="chat-card">
        <div className="chat-card-h">
          <span className="mono">{id ?? '작업'}</span>
          <State v={c ? codingState(c) : CODING_LABEL[status] ?? status} live={isRunning(status)} />
        </div>
        <div className="chat-card-row">
          {files > 0 ? <Tag>{files}파일</Tag> : <span />}
          {id && (
            <span className="tags">
              <CancelButton taskId={id} status={c?.status} />
              <ResumeButton taskId={id} status={c?.status} />
              <button type="button" className="btn-ghost" onClick={() => { onOpen(); go(toPath(`work/prs/${encodeURIComponent(id)}`)) }}>작업 보기</button>
            </span>
          )}
        </div>
      </div>
    </>
  )
}
