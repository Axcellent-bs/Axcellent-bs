// 소통 (슬랙 벤치마킹): 왼쪽 채널 목록, 가운데 대화, 오른쪽 채널 정보.
// 부서 채널에 쓴 메시지는 그 부서 창구(다른 부서 페이지)로 가고, 메시지는 결정으로 기록하거나 정리 문서에 옮길 수 있다.
// 에이전트 대화: 사건·요청 하나(context_id)에서 에이전트끼리 주고받은 A2A·이벤트·execute 기록을 읽기 전용으로 본다.
import { useEffect, useRef, useState, type Dispatch, type KeyboardEvent } from 'react'
import {Select, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, type Action, type Activity, type Dept, type Message, type State, type Thread } from './data'
import { Linked, Ref } from './links'
import { Dep, Grade, Point, Tag } from './parts'
import { useCommunications } from './comms'

// 채널마다 마지막으로 본 메시지 수 (화면을 옮겨도 유지)
const seen = new Map<string, number>()

const channelName = (t: Thread) => (t.kind === 'team' ? '기획개발팀' : t.kind === 'dept' ? t.title : t.on.toLowerCase())
const topic = (t: Thread) =>
  t.kind === 'team' ? '기획개발팀 내부 대화. 에이전트 알림도 여기에 온다.'
    : t.kind === 'dept' ? `${t.title}와 주고받는 채널. 여기 쓴 메시지는 ${t.title} 창구로 간다.`
      : `${t.title} 문서 스레드. 여기서 나온 결정은 문서 결정 이력에 남는다.`

// context_id별 에이전트 대화 (최근에 움직인 순)
export interface Convo { ctx: string; title: string; items: Activity[]; depts: Dept[] }
export function convosOf(s: State): Convo[] {
  const by = new Map<string, Activity[]>()
  for (const a of s.activity) by.set(a.ctx, [...(by.get(a.ctx) ?? []), a])
  // 장애 사건이면 사건 이름, 요청이면 요청 이름
  const title = (ctx: string) => {
    const inc = s.incidents.find((x) => x.ctx === ctx)
    if (inc) return `장애 · ${inc.title}`
    const r = s.requests.find((x) => x.ctx === ctx)
    return r ? `${r.id} ${r.title}` : ctx
  }
  return Array.from(by, ([ctx, items]) => ({ ctx, title: title(ctx), items, depts: Array.from(new Set(items.flatMap((a) => [a.from, a.to]))) }))
    .sort((a, b) => s.activity.lastIndexOf(b.items[b.items.length - 1]) - s.activity.lastIndexOf(a.items[a.items.length - 1]))
}

export function Talk({ s: state, d, item }: { s: State; d: Dispatch<Action>; item?: string }) {
  // 실제 모드면 에이전트 대화는 공통 로그(dashboard-api)에서, 목 모드면 연동 버스 활동에서
  const live = useCommunications('planning')
  const s = live ? { ...state, activity: live } : state
  const convos = convosOf(s)
  const convo = item?.startsWith('ctx_') ? convos.find((c) => c.ctx === item) : undefined
  return (
    <div className="slack">
      <aside className="sl-side" aria-label="채널">
        <div className="sl-ws">기획개발 소통</div>
        <Group label="채널" list={s.threads.filter((t) => t.kind === 'team')} cur={item} fallback={!convo} />
        <Group label="부서" list={s.threads.filter((t) => t.kind === 'dept')} cur={item} />
        <Group label="문서 스레드" list={s.threads.filter((t) => !t.kind || t.kind === 'spec')} cur={item} />
        <div className="sl-group">
          <div className="sl-group-h">에이전트 대화 <span className="muted">{convos.length}</span></div>
          {convos.map((c) => (
            <a key={c.ctx} href={`#/talk/${c.ctx}`} className={`sl-ch${c.ctx === convo?.ctx ? ' on' : ''}`} title={c.title}>
              <span className="sl-hash">⇄</span>
              <span className="sl-ch-name">{c.title}</span>
              <span className="sl-count">{c.items.length}</span>
            </a>
          ))}
        </div>
      </aside>
      {convo ? <ConvoView s={s} c={convo} /> : (() => {
        const th = s.threads.find((t) => t.id === item) ?? s.threads.find((t) => t.id === 'CH-team') ?? s.threads[0]
        return th ? <ThreadView key={th.id} s={s} d={d} th={th} /> : <div className="empty"><b>대화 없음</b></div>
      })()}
    </div>
  )
}

function Group({ label, list, cur, fallback }: { label: string; list: Thread[]; cur?: string; fallback?: boolean }) {
  const isOn = (t: Thread) => t.id === cur || (fallback && !cur && t.id === 'CH-team')
  return (
    <div className="sl-group">
      <div className="sl-group-h">{label}</div>
      {list.map((t) => {
        const unread = isOn(t) ? 0 : Math.max(0, t.messages.length - (seen.get(t.id) ?? t.messages.length))
        return (
          <a key={t.id} href={`#/talk/${t.id}`} className={`sl-ch${isOn(t) ? ' on' : ''}${unread ? ' unread' : ''}`}>
            <span className="sl-hash">{t.kind === 'dept' ? '●' : '#'}</span>
            <span className="sl-ch-name">{channelName(t)}</span>
            {unread > 0 && <span className="sl-badge">{unread}</span>}
          </a>
        )
      })}
    </div>
  )
}

function ThreadView({ s, d, th }: { s: State; d: Dispatch<Action>; th: Thread }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [docId, setDocId] = useState(s.docs[s.docs.length - 1]?.id ?? '')
  const end = useRef<HTMLDivElement>(null)

  for (const t of s.threads) if (!seen.has(t.id)) seen.set(t.id, t.messages.length)
  useEffect(() => {
    seen.set(th.id, th.messages.length)
    end.current?.scrollIntoView({ block: 'end' })
  }, [th.id, th.messages.length])

  const spec = th.on ? s.specs.find((x) => x.id === th.on) : undefined
  const decisions = s.decisions.filter((x) => x.from.includes(th.title) || (th.on && x.spec === th.on))
  const peerConvos = th.peer ? convosOf(s).filter((c) => c.depts.includes(th.peer!)) : []
  const send = () => {
    if (!text.trim()) return
    d({ type: 'postMessage', thread: th.id, text })
    if (th.peer) toast.show(`${DEPT_NAME[th.peer]} 창구로 보냄`)
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      <section className="sl-main">
        <header className="sl-head">
          <div><b>{th.kind === 'dept' ? '' : '# '}{channelName(th)}</b> <span className="muted small">{topic(th)}</span></div>
          {spec && <a href={`#/work/specs/${spec.id}`} className="small">{spec.id} 문서 열기</a>}
        </header>
        <div className="sl-msgs">
          <div className="sl-day"><span>오늘</span></div>
          {th.messages.length === 0 && <div className="sl-empty"><b># {channelName(th)}</b> 채널의 첫 대화를 시작하세요.</div>}
          {th.messages.map((m, i) => (
            <Row key={i} s={s} m={m} cont={i > 0 && th.messages[i - 1].who === m.who}>
              <div className="sl-tools">
                {!m.decision && <button type="button" onClick={() => { d({ type: 'extractDecision', thread: th.id, index: i }); toast.show('결정으로 기록 → 인사에 ① 전송') }}>결정으로 기록</button>}
                {docId && <button type="button" onClick={() => { d({ type: 'addBlock', doc: docId, kind: 'p', text: `${m.who}: ${m.text}` }); toast.show(`${docId}에 추가`) }}>문서에 추가</button>}
              </div>
            </Row>
          ))}
          {th.on === 'SPEC-7' && spec?.question && (
            <div className="question" style={{ margin: '8px 20px' }}>
              질문 · {spec.question}
            </div>
          )}
          <div ref={end} />
        </div>
        <div className="sl-composer">
          <textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} rows={2} placeholder={`${th.kind === 'dept' ? '' : '#'}${channelName(th)}에 메시지 보내기`} aria-label="메시지" />
          <div className="sl-composer-bar">
            <span className="muted small">Enter 보내기 · Shift+Enter 줄바꿈{th.peer ? ` · ${DEPT_NAME[th.peer]} 창구로 전달` : ''}</span>
            <button type="button" className="send" disabled={!text.trim()} onClick={send} aria-label="보내기">↑</button>
          </div>
        </div>
      </section>

      <aside className="sl-info">
        <div className="sl-info-h">채널 정보</div>
        <p className="small" style={{ margin: 0 }}>{topic(th)}</p>
        {th.peer && <Tag>{DEPT_NAME[th.peer]} 창구 연결</Tag>}
        {th.peer && (
          <>
            <div className="sl-info-h">{DEPT_NAME[th.peer]} 에이전트와의 대화 {peerConvos.length}건</div>
            {peerConvos.length ? peerConvos.map((c) => <a key={c.ctx} href={`#/talk/${c.ctx}`} className="small">⇄ {c.title}</a>) : <span className="muted small">아직 없음</span>}
          </>
        )}
        <div className="sl-info-h">문서에 옮길 곳</div>
        <Select value={docId} onChange={(e) => setDocId(e.target.value)} options={s.docs.slice().reverse().map((x) => ({ value: x.id, label: `${x.id} ${x.title}` }))} />
        {docId && <a href={`#/docs/${docId}`} className="small">{docId} 열기</a>}
        <div className="sl-info-h">이 채널의 결정 {decisions.length}건</div>
        {decisions.length ? decisions.map((x) => <div key={x.id} className="small"><Ref s={s} id={x.id} /> {x.text}</div>) : <span className="muted small">아직 없음</span>}
      </aside>
    </>
  )
}

// 에이전트끼리 주고받은 기록. 사람이 끼어들 때는 상대 부서 채널에 쓴다
function ConvoView({ s, c }: { s: State; c: Convo }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [c.ctx, c.items.length])
  const peers = c.depts.filter((x) => x !== 'planning')
  const peerChannel = (x: Dept) => s.threads.find((t) => t.peer === x)
  const kinds = Array.from(new Set(c.items.map((a) => a.kind)))
  return (
    <>
      <section className="sl-main">
        <header className="sl-head">
          <div><b>⇄ {c.title}</b> <span className="muted small mono">{c.ctx}</span></div>
          <Ref s={s} id={c.ctx} className="small">{c.ctx.startsWith('ctx_inc_') ? '사건 상세' : '프로젝트 보기'}</Ref>
        </header>
        <div className="sl-msgs">
          <div className="sl-day"><span>에이전트 대화 · 읽기 전용</span></div>
          {c.items.map((a, i) => (
            <div key={i} className={`sl-row agent${i > 0 && c.items[i - 1].from === a.from ? ' cont' : ''}`}>
              <div className={`sl-av bot${a.from === 'planning' ? ' mine' : ''}`} aria-hidden>{DEPT_NAME[a.from].slice(0, 1)}</div>
              <div className="sl-body">
                <div className="sl-meta">
                  <Dep d={a.from} /> <span className="arrow">→</span> <Dep d={a.to} /> <span className="muted small">{a.t}</span>
                </div>
                <div className="sl-text">
                  <Point>{a.point}</Point> <span className="kind">{a.kind}</span> <Grade g={a.grade} /> <Linked s={s} text={a.text} />
                </div>
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
        <div className="sl-composer readonly">
          <span className="muted small">
            에이전트끼리 주고받은 기록이라 여기서는 쓰지 않습니다. 사람이 끼어들려면 상대 부서 채널에 쓰세요:{' '}
            {peers.map((x) => { const t = peerChannel(x); return t ? <a key={x} href={`#/talk/${t.id}`}>{DEPT_NAME[x]} </a> : null })}
          </span>
        </div>
      </section>

      <aside className="sl-info">
        <div className="sl-info-h">대화 정보</div>
        <p className="small" style={{ margin: 0 }}>같은 사건·요청(context_id)에서 오간 연결 지점 {c.items.length}건. 원천은 각 부서 에이전트의 A2A Task·이벤트·execute() 기록이다.</p>
        <div className="sl-info-h">참여 부서 {c.depts.length}</div>
        {c.depts.map((x) => <div key={x} className="small"><Dep d={x} /> {c.items.filter((a) => a.from === x).length}건 보냄</div>)}
        <div className="sl-info-h">방식</div>
        <div className="tags">{kinds.map((k) => <Tag key={k}>{k} {c.items.filter((a) => a.kind === k).length}</Tag>)}</div>
        <div className="sl-info-h">사람 확인이 필요한 것</div>
        {c.items.some((a) => a.grade === 'C') ? c.items.filter((a) => a.grade === 'C').map((a, i) => <div key={i} className="small"><Linked s={s} text={a.text} /></div>) : <span className="muted small">없음 (C 등급 0건)</span>}
      </aside>
    </>
  )
}

function Row({ s, m, cont, children }: { s: State; m: Message; cont: boolean; children: React.ReactNode }) {
  return (
    <div className={`sl-row${cont ? ' cont' : ''}`}>
      <div className={`sl-av${m.dept === 'planning' ? ' mine' : ''}`} aria-hidden>{cont ? '' : m.who.slice(0, 1)}</div>
      <div className="sl-body">
        {!cont && (
          <div className="sl-meta">
            <b>{m.who}</b> <span className="muted small">{DEPT_NAME[m.dept]}{m.t ? ` · ${m.t.slice(6)}` : ''}</span>
          </div>
        )}
        <div className="sl-text"><Linked s={s} text={m.text} /> {m.decision && <Tag tone="green">결정 {m.decision}</Tag>}</div>
      </div>
      {children}
    </div>
  )
}
