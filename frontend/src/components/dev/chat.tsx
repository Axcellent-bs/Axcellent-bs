// AI와 대화: 개발 에이전트와 대화하다 필요한 것이 모이면 에이전트가 코딩 작업(→ PR)을 시작한다.
// 대화는 REST(/dev-chat, 서버가 계정을 확인)로 주고받는다. SSE의 chats에는 원문 없이 id·상태·시각만 있어서
// 그게 바뀌면 /dev-chat을 다시 읽는다. SSE가 없으면 2초마다 다시 읽는다
// (SSE가 있어도 답을 기다리는 동안은 4초마다 다시 읽는다: 갱신을 놓쳐 "생각 중"이 안 풀리는 일 막기)
// 보내면 서버가 말을 적고 answering으로 바로 돌려준다. 답은 뒤에서 붙는다 (SSE·다시 읽기로 본다)
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Chip, useToast, type ChipTone } from '@dejafix/common-frontend'
import type { ChatMessage, ChatStatus, ChatThread, CodingMode, CodingStatus, State as S, Task } from '@/lib/planningData'
import { DEV_API, PlanningApiError, chatApi, needAccount, codingSettingsApi, useMe, vcsApi, type CodingSettings, type VcsConnection } from '@/lib/planning'
import { CODING_LABEL, CancelButton, ResumeButton, codingState, isRunning } from '@/components/dev/coding'
import { State } from '@/components/planning-common/parts'
import { go, toPath, idText } from '@/components/planning-common/links'
import { Tag } from '@/components/planning-common/parts'
import { humanizePlanIds } from '@/lib/format'
import { canClose, chatNotice, mergeThreads, needsTools, pollInterval, sendErrorText, stampKey, viewedPr } from '@/lib/devChatView'
import { useLocation } from 'react-router'
import { useSession } from '@/demo/session'
import { InlineConfirm } from '@/components/InlineConfirm'

const api = chatApi(DEV_API)

const STATUS: Record<ChatStatus, { label: string; tone: ChipTone }> = {
  talking: { label: '대화 중', tone: 'neutral' },
  handed: { label: '기획 확인 대기', tone: 'amber' },
  started: { label: '작업 중', tone: 'green' },
  blocked: { label: '막힘', tone: 'amber' },
  waiting: { label: '앞 요청 기다림', tone: 'amber' },
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
const SETTINGS_RELOAD_MS = 60_000 // 패널을 연 동안 설정·레포 연결을 다시 읽는 간격

const taskOf = (s: S, t: ChatThread): Task | undefined => (t.taskId ? s.tasks.find((x) => x.id === t.taskId) : undefined)

/** 작업이 아직 돌고 있는가. SSE에 작업이 아직 없으면 방금 시작한 것만 돈다고 본다 */
function running(s: S, t: ChatThread): boolean {
  if (t.status !== 'started') return false
  const c = taskOf(s, t)?.coding
  if (c) return RUNNING.includes(c.status)
  return Date.now() - new Date(t.updatedAt).getTime() < RECENT_MS
}

/** 헤더 칩과 패널이 같이 쓰는 대화 상태. 패널을 닫았다 열어도 남는다.
 *  지금 계정이 연 대화만 다룬다 (서버가 계정을 확인해 그 계정 대화만 주고, 여기서도 한 번 더 거른다) */
export function useDevChat(s: S, on: boolean) {
  // 세션을 읽는 중이면 null: 목록을 읽거나 보내지 않는다 ('guest'로 보내지 않는다)
  const account = useSession().account?.id ?? null
  const [local, setLocal] = useState<ChatThread[]>([])
  const [curId, setCurId] = useState<string | null | undefined>(undefined) // undefined = 아직 안 고름(가장 최근 대화)
  const [draft, setDraft] = useState('')
  const [hidden, setHidden] = useState<string[]>([]) // 방금 숨긴 대화 (SSE가 따라오기 전에도 빼 둔다)
  // 계정이 바뀔 때만 고른 대화를 비운다. 서버 연결이 끊겼다 다시 붙으면 고른 대화는 그대로 두고 목록만 다시 읽는다
  useEffect(() => {
    setLocal([])
    setCurId(undefined)
    setHidden([])
  }, [account])
  // SSE 대화 바뀜 표시(id·상태·시각)가 바뀌면 다시 읽는다. 늦게 온 다른 계정 목록은 버린다
  // 고른 대화가 목록 상한(최근 20개) 밖이어도 바뀌면 그 대화를 id로 다시 읽는다
  const stamp = stampKey(s.chats)
  const picked = useRef<string | null | undefined>(undefined)
  picked.current = curId
  useEffect(() => {
    if (!on || !account) return
    let alive = true
    api.list(account).then((rows) => {
      if (!alive) return
      setLocal((prev) => mergeThreads(prev, rows))
      const id = picked.current
      if (id && !rows.some((t) => t.id === id)) {
        api.get(id, account).then((t) => { if (alive) setLocal((prev) => mergeThreads(prev, [t])) }).catch(() => undefined)
      }
    }).catch(() => undefined)
    return () => { alive = false }
  }, [on, account, stamp])
  const threads = mergeThreads(local, []).filter((t) => t.account === account && !hidden.includes(t.id))
  const upsert = (t: ChatThread) => setLocal((prev) => [t, ...prev.filter((x) => x.id !== t.id)])
  const cur = curId === null ? undefined : threads.find((t) => t.id === (curId ?? threads[0]?.id))
  return {
    account, threads, cur, upsert, draft, setDraft,
    select: (id: string | null) => setCurId(id),
    /** 숨긴 대화를 화면에서 빼고 새 대화로 돌아간다 */
    drop: (id: string) => {
      setHidden((prev) => [...prev, id])
      setLocal((prev) => prev.filter((x) => x.id !== id))
      setCurId(null)
    },
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
  const [connsRead, setConnsRead] = useState(false) // 레포 연결 목록을 읽었나 (못 읽으면 "없음"이라 하지 않는다)
  const [acting, setActing] = useState(false) // 끝내기·숨기기 보내는 중
  const [askRemove, setAskRemove] = useState(false) // 11/24 숨기기 확인은 화면 안 상자(InlineConfirm)
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  // 설정(서버 AI 키)·레포 연결은 패널을 연 동안 다시 읽는다: 도구 연결에서 키·레포를 넣고 돌아오거나
  // (창 포커스) 다른 사람이 바꿨을 때 (SETTINGS_RELOAD_MS마다, 화면이 보일 때만) 안내가 풀린다
  useEffect(() => {
    const load = () => {
      codingSettingsApi(DEV_API).get().then(setSetting).catch(() => setSetting(null))
      vcsApi(DEV_API).list().then((c) => {
        setConns(c)
        setConnsRead(true)
        setConn((x) => x || c[0]?.id || '')
      }).catch(() => setConns([]))
    }
    load()
    const onFocus = () => { if (!document.hidden) load() }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    const t = setInterval(onFocus, SETTINGS_RELOAD_MS)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
      clearInterval(t)
    }
  }, [])
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  useEffect(() => setErr(null), [cur?.id])

  const answering = cur?.status === 'answering' // 서버가 답을 만드는 중
  const ended = cur?.status === 'closed'
  const busy = !!pending || answering
  // AI 미설정(서버 기본 모델 없음)이면 보내기를 막고 안내한다. 레포가 없으면 안내만 (넘기기는 서버가 받는다)
  const notice = chatNotice(setting ? setting.model_default : undefined, connsRead ? conns.length : null)
  const noLlm = notice === 'no_llm'
  // SSE가 없으면 답·작업을 기다리는 동안, SSE가 있어도 답을 기다리는 동안은 다시 읽는다
  const poll = cur ? pollInterval(chat.live, busy, running(s, cur)) : null
  useEffect(() => {
    const account = chat.account
    if (!poll || !cur || !account) return
    const id = cur.id
    // 다른 탭을 볼 때는 받지 않고, 앞 요청이 아직이면 이번 차례는 건너뛴다
    let busy = false
    const t = setInterval(() => {
      if (busy || document.hidden) return
      busy = true
      api.get(id, account).then(chat.upsert).catch(() => undefined).finally(() => { busy = false })
    }, poll)
    return () => clearInterval(t)
  }, [poll, cur?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const msgs = cur?.messages ?? []
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight })
  }, [msgs.length, busy, err])

  const repo = cur?.connection ?? conn
  // 지금 보는 PR (레포 PR·AI PR 화면). 같이 보내면 서버가 그 PR의 CI 상태·실패 로그를 읽어 답한다
  const loc = useLocation()
  const pr = viewedPr(loc.pathname, loc.search, s.prs)
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
    if (!message || busy || ended || noLlm) return
    setPending(message)
    setErr(null)
    setDraft('')
    try {
      const t = await api.send({ ...(cur ? { thread_id: cur.id } : {}), message, ...(repo ? { connection: repo } : {}), ...(pr ? { pr } : {}), mode, account: needAccount(chat.account) })
      chat.upsert(t)
      chat.select(t.id)
    } catch (x) {
      const code = x instanceof PlanningApiError ? x.code : 'NETWORK'
      const msg = sendErrorText(code, x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요')
      setErr({ code, message: msg })
      setDraft(message)
      toast.show(msg)
    } finally {
      setPending(null)
      input.current?.focus()
    }
  }
  // 끝내기(closed)·숨기기: 띄운 작업은 그대로 돈다
  const act = async (kind: 'close' | 'remove') => {
    if (!cur || acting) return
    setAskRemove(false)
    setActing(true)
    try {
      if (kind === 'close') chat.upsert(await api.close(cur.id, needAccount(chat.account)))
      else chat.drop(await api.remove(cur.id, needAccount(chat.account)))
    } catch (x) {
      toast.show(x instanceof PlanningApiError ? sendErrorText(x.code, x.message) : '개발·배포 서버에 연결할 수 없어요')
    } finally {
      setActing(false)
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
          {threads.map((t) => <option key={t.id} value={t.id}>{`${t.title || idText(t.id)} · ${STATUS[t.status]?.label ?? t.status}`}</option>)}
        </select>
        <button type="button" className="btn-ghost" onClick={() => chat.select(null)} disabled={!!pending || !cur}>새 대화</button>
        <button type="button" className="btn-ghost" onClick={() => void act('close')} disabled={!!pending || acting || !canClose(cur)} title="대화를 끝내요. 띄운 작업은 그대로 돌아요">끝내기</button>
        <button type="button" className="btn-ghost" onClick={() => setAskRemove(true)} disabled={!!pending || acting || !cur} title="목록에서 숨겨요. 띄운 작업은 그대로 돌아요">숨기기</button>
      </div>
      {askRemove && cur && (
        <div className="px-3">
          <InlineConfirm message="이 대화를 목록에서 숨길까요? 띄운 작업은 그대로 돌아요." confirmLabel="숨기기" busy={acting}
            onConfirm={() => void act('remove')} onCancel={() => setAskRemove(false)} />
        </div>
      )}
      {conns.length > 1 && (
        <div className="chat-drawer-bar">
          <span className="muted small">레포</span>
          <select className="text-input chat-sel mono" value={repo} onChange={(e) => setConn(e.target.value)} aria-label="대상 레포" disabled={!!pending || !!cur?.connection}>
            {conns.map((c) => <option key={c.id} value={c.id}>{c.repo}</option>)}
          </select>
        </div>
      )}

      <div className="chat-drawer-msgs" ref={box} aria-live="polite">
        {noLlm && (
          <div className="bubble ai err">
            서버 AI가 설정되지 않아 대화할 수 없어요. 도구 연결 &gt; 고객사 키에서 AI 키를 넣어 주세요.
            <Tools onTools={onTools} />
          </div>
        )}
        {notice === 'no_repo' && (
          <div className="bubble ai q">
            연결된 레포가 없어요. 대화와 기획 넘기기는 되지만, 개발이 시작되려면 레포 연결이 필요해요.
            <Tools onTools={onTools} />
          </div>
        )}
        {!msgs.length && !pending && (
          <div className="chat-examples">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" className="q-chip" onClick={() => { setDraft(x); input.current?.focus() }}>{x}</button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <Msg key={m.id} m={m} s={s} onOpen={onClose} onTools={onTools} viewOnly={!!cur?.viewOnly}
            onGo={i === msgs.length - 1 && m.kind === 'plan' && !!m.plan && cur?.status === 'talking' && !busy && !ended ? () => void send('진행해') : undefined} />
        ))}
        {pending && msgs[msgs.length - 1]?.text !== pending && <div className="bubble user">{pending}</div>}
        {busy && <div className="bubble ai"><span className="typing" aria-label="답 쓰는 중"><i /><i /><i /></span></div>}
        {err && (
          <div className="bubble ai err">
            {err.message}
            {needsTools(err.code) && <Tools onTools={onTools} />}
          </div>
        )}
        {!busy && !err && cur?.lastError && <div className="bubble ai err">{cur.lastError}</div>}
        {!busy && !err && cur?.status === 'blocked' && <Tools onTools={onTools} />}
      </div>

      <div className="sl-composer">
        <textarea ref={input} rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
          placeholder={noLlm ? '서버 AI 설정이 필요해요' : ended ? '끝난 대화예요. 새 대화를 여세요' : cur ? '답장' : '무엇을 바꿀까요'} aria-label="메시지" disabled={busy || ended || !connected || noLlm} />
        <div className="sl-composer-bar">
          <div className="seg-toggle" role="group" aria-label="누가 코딩하나" title={cur?.taskId ? '작업을 띄운 대화는 바꿀 수 없어요' : '서버 AI는 회사 토큰, 내 Claude는 내 구독으로 코딩해요'}>
            <button type="button" aria-pressed={mode === 'agent'} onClick={() => choose('agent')} disabled={busy || !!cur?.taskId || (trialOut && !noClaude)} title={trialOut ? `서버 AI 체험 한도 다 씀 (${setting!.trial_used.toLocaleString()}/${setting!.trial_limit.toLocaleString()} 토큰)` : undefined}>서버 AI</button>
            <button type="button" aria-pressed={mode === 'subscription'} onClick={() => choose('subscription')} disabled={busy || !!cur?.taskId || noClaude} title={noClaude ? '연결된 내 Claude 없음 (연결 메뉴에서 추가)' : undefined}>내 Claude</button>
          </div>
          <button type="button" className="send" onClick={() => void send()} disabled={busy || ended || !connected || noLlm || !draft.trim()} aria-label="보내기">↑</button>
        </div>
      </div>
    </aside>
  )
}

const Tools = ({ onTools }: { onTools: () => void }) => (
  <div><button type="button" className="btn-ghost" onClick={onTools}>개발 도구 연결</button></div>
)

// onGo: 마지막 계획 카드의 "이대로 진행" (누르면 "진행해"를 보내 기획에 넘긴다)
// onTools: AI 키 오류(401·403) 메시지에 붙이는 도구 연결 버튼
// viewOnly: 남이 낸 같은 요청의 작업을 잇는 대화. 작업 카드의 중단·재개 버튼을 숨긴다 (서버 작업 API도 거절한다)
function Msg({ m, s, onOpen, onGo, onTools, viewOnly }: { m: ChatMessage; s: S; onOpen: () => void; onGo?: () => void; onTools: () => void; viewOnly: boolean }) {
  if (m.role === 'user') return <div className="bubble user">{humanizePlanIds(m.text)}</div>
  if (m.kind === 'error') return <div className="bubble ai err">{humanizePlanIds(m.text)}{needsTools(null, m.text) && <Tools onTools={onTools} />}</div>
  if (m.kind === 'question' || m.kind === 'waiting') return <div className="bubble ai q">{humanizePlanIds(m.text)}</div>
  if (m.kind === 'plan') {
    const p = m.plan
    return (
      <>
        {m.text && <div className="bubble ai">{humanizePlanIds(m.text)}</div>}
        {p && (
          <div className="chat-card">
            <div className="chat-card-h">{p.title}</div>
            {!!p.done.length && <ul>{p.done.map((d, i) => <li key={i}>{d}</li>)}</ul>}
            {!!p.paths.length && <div className="chat-paths">{p.paths.map((x) => <span key={x} className="point">{x}</span>)}</div>}
            {onGo && <div className="chat-card-row"><span /><button type="button" className="btn-primary" onClick={onGo}>이대로 진행</button></div>}
          </div>
        )}
      </>
    )
  }
  if (m.kind === 'started') return <Started m={m} s={s} onOpen={onOpen} viewOnly={viewOnly} />
  return <div className="bubble ai">{humanizePlanIds(m.text)}</div>
}

function Started({ m, s, onOpen, viewOnly }: { m: ChatMessage; s: S; onOpen: () => void; viewOnly: boolean }) {
  const id = m.taskId
  const task = id ? s.tasks.find((t) => t.id === id) : undefined
  const c = task?.coding
  const status: CodingStatus = c?.status ?? 'queued'
  const files = c?.files?.length ?? 0
  return (
    <>
      {m.text && <div className="bubble ai">{humanizePlanIds(m.text)}</div>}
      <div className="chat-card">
        <div className="chat-card-h">
          <span className="mono">{id ?? '작업'}</span>
          <State v={c ? codingState(c) : CODING_LABEL[status] ?? status} live={isRunning(status)} />
        </div>
        <div className="chat-card-row">
          {files > 0 ? <Tag>{files}파일</Tag> : <span />}
          {id && (
            <span className="tags">
              {!viewOnly && <CancelButton taskId={id} status={c?.status} />}
              {!viewOnly && <ResumeButton taskId={id} status={c?.status} discarded={task?.discarded} />}
              <button type="button" className="btn-ghost" onClick={() => { onOpen(); go(toPath(`work/prs/${encodeURIComponent(id)}`)) }}>작업 보기</button>
            </span>
          )}
        </div>
      </div>
    </>
  )
}
