// 기획서 화면: 문서가 원본이고 문단에 작업·PR·결정이 붙는다
import { useState, type Dispatch } from 'react'
import { Button, Notice, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, nextDocId, type Action, type Decision, type PR, type Spec, type State as S } from '@/lib/planningData'
import { ActivityList, Sec, State, Tag, rowNav } from '@/components/planning-common/parts'
import { Linked, Ref, go, hrefOf, prSlug, reviewOf, toPath } from '@/components/planning-common/links'
import { CONFIRMED_BY, mergedLabel } from '@/components/planning-common/flow'
import { RepoTag } from '@/components/planning-common/repo'
import { Pager, usePage } from '@/components/planning-common/pager'
import { useAllReleaseChecks, useReleaseChecks, type ReleaseCheck } from '@/lib/planning'

interface P { s: S; d: Dispatch<Action> }

// ---------------------------------------------------------------- 확인 대기 (승인 = 확정, 거절 = 이유 남기고 대기열에서 내림)
function ReviewDecide({ d, spec, xr }: { d: Dispatch<Action>; spec: Spec; xr: NonNullable<ReturnType<typeof reviewOf>> }) {
  const toast = useToast()
  const [reason, setReason] = useState<string | null>(null)
  const human = xr.lane === 'human'
  return (
    <Notice tone="warn">
      <div className="xr-decide">
        <span>{human ? `사람 확인 · 되돌릴 수 없음 ${xr.irreversible}` : `교차검증 · ${xr.routeWhy.join(', ')}`} <span className="mono muted">{xr.id}</span></span>
        <span style={{ flex: 1 }} />
        <Button variant="primary" onClick={() => { d({ type: 'humanDecide', review: xr.id, ok: true }); toast.show(`${spec.id} 승인`) }}>승인</Button>
        <Button onClick={() => setReason(reason === null ? '' : null)}>거절</Button>
      </div>
      {reason !== null && (
        <form className="xr-decide" onSubmit={(e) => { e.preventDefault(); if (!reason.trim()) return; d({ type: 'rejectSpec', spec: spec.id, reason: reason.trim() }); setReason(null); toast.show(`${spec.id} 거절`) }}>
          <input className="input" style={{ flex: 1 }} autoFocus placeholder="거절 이유" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button variant="primary" type="submit" disabled={!reason.trim()}>거절 보내기</Button>
        </form>
      )}
    </Notice>
  )
}

// ---------------------------------------------------------------- 배포 전 확인 (개발이 다 됐다고 하면 기획서와 맞춰 본 결과)
// 칩은 "무엇이 걸렸는지"만 보이고, 맞는 문단은 하나로 묶는다. 판정 코드는 화면에 내보내지 않는다
const SPEC_BLOCK: Record<string, string> = {
  spec_not_confirmed: '기획서 미확정', open_question: '질문 남음', spec_unknown: '기획서 없음', pr_outside_spec: '기획서 밖 PR',
}
const PARA_BLOCK = { missing_task: '작업 없음', not_merged: 'PR 머지 전', incident_open: '장애 열림' } as const
// 상태 칩은 "왜 멈췄는지"를 바로 말한다. 여러 개면 먼저 풀어야 할 것 하나 + "외 N"
const HOLD_STATE: [string, string][] = [
  ['spec_unknown', '기획서 없음'],
  ['spec_not_confirmed', '기획서 확정 전'],
  ['open_question', '질문 답 대기'],
  ['linked_incident_open', '장애 처리 대기'],
  ['para_without_task', '작업 빠짐'],
  ['task_not_merged', 'PR 머지 대기'],
  ['pr_outside_spec', '기획서 밖 PR 섞임'],
  ['paused_by_human', '사람이 중단'],
]
export function releaseState(rc: ReleaseCheck): { label: string; tone: 'green' | 'amber'; title?: string } {
  if (rc.order_sent) return { label: '배포 지시 보냄', tone: 'green' }
  if (rc.verdict === 'ready') return { label: '배포 지시 전송 실패', tone: 'amber', title: rc.order_result ?? undefined }
  if (rc.verdict === 'needs_human') {
    const diff = rc.items.filter((i) => i.content === 'mismatch').length
    const none = rc.items.filter((i) => i.content === 'none').length
    const label = diff ? '기획서와 다른 PR 확인' : '검수 안 된 PR 확인'
    return { label: diff && none ? `${label} 외 1` : label, tone: 'amber' }
  }
  const hits = HOLD_STATE.filter(([code]) => rc.hold_reasons.includes(code)).map(([, label]) => label)
  if (!hits.length) return { label: '배포 보류', tone: 'amber' }
  return { label: hits.length > 1 ? `${hits[0]} 외 ${hits.length - 1}` : hits[0], tone: 'amber', title: hits.join(', ') }
}
function ReleaseCheckBox({ d, spec }: { d: Dispatch<Action>; spec: string }) {
  const toast = useToast()
  const [rc] = useReleaseChecks(spec)
  if (!rc) return null
  const st = releaseState(rc)
  const ok = rc.items.filter((i) => i.status === 'covered' && i.content === 'match').map((i) => i.para_id)
  const blocks: { key: string; label: string; title?: string }[] = [
    ...rc.hold_reasons.filter((h) => SPEC_BLOCK[h]).map((h) => ({ key: h, label: SPEC_BLOCK[h] })),
    ...rc.items.filter((i) => i.status !== 'covered').map((i) => ({ key: `${i.para_id}-s`, label: `${i.para_id} ${PARA_BLOCK[i.status as keyof typeof PARA_BLOCK]}` })),
    ...rc.items.filter((i) => i.status === 'covered' && i.content === 'mismatch').map((i) => ({ key: `${i.para_id}-c`, label: `${i.para_id} 기획서와 다름`, title: i.content_notes.join('\n') })),
    ...rc.items.filter((i) => i.status === 'covered' && i.content === 'none').map((i) => ({ key: `${i.para_id}-n`, label: `${i.para_id} 검수 결과 없음` })),
  ]
  return (
    <Notice tone={st.tone === 'amber' ? 'warn' : undefined}>
      <div className="xr-decide">
        <b>배포 전 확인</b>
        <Tag tone={st.tone} title={st.title}>{st.label}</Tag>
        <span className="muted small">{rc.env} · PR {rc.pr_numbers.map((n) => `#${n}`).join(', ')} · {rc.check_id}</span>
        <span style={{ flex: 1 }} />
        {rc.verdict === 'needs_human' && !rc.order_sent && (
          <Button variant="primary" onClick={() => { d({ type: 'sendReleaseOrder', check: rc.check_id }); toast.show(`${rc.check_id} 배포 지시`) }}>확인하고 배포 지시</Button>
        )}
      </div>
      {(blocks.length > 0 || ok.length > 0) && (
        <div className="xr-decide" style={{ flexWrap: 'wrap' }}>
          {blocks.map((b) => <Tag key={b.key} tone="amber" title={b.title}>{b.label}</Tag>)}
          {ok.length > 0 && <Tag tone="green">{ok.join('·')} 기획서대로</Tag>}
        </div>
      )}
    </Notice>
  )
}

// ---------------------------------------------------------------- 기획 문서 (문서가 원본, 문단에 모든 것이 붙는다)
// 기획서 탭 첫 화면 = 기획서 목록 (최근 것이 위). 줄을 누르면 그 기획서 상세로
export function Specs({ s, d, item, item2 }: P & { item?: string; item2?: string }) {
  if (!item) return <SpecList s={s} d={d} />
  const spec = s.specs.find((x) => x.id === item)
  if (!spec) return <div className="empty"><b>{item} 없음</b> <a href={toPath('work/specs')}>기획서 목록</a></div>
  return <SpecView key={spec.id} s={s} d={d} spec={spec} item2={item2} />
}

type ListKind = 'all' | 'spec' | 'doc'
type ListRow = { key: string; kind: 'spec' | 'doc'; href: string; id: string; title: string; from?: S['requests'][number]['from']; state: string; size: string; tail?: { label: string; tone: 'green' | 'amber'; title?: string }; note?: string }
// 문서·기획서 목록 (탭 첫 화면). 기획서(요청에서 나온 것)와 사람이 쓴 문서를 한 목록에, 최근 것이 위
function SpecList({ s, d }: { s: S; d: Dispatch<Action> }) {
  const checks = useAllReleaseChecks()
  const [kind, setKind] = useState<ListKind>('all')
  const specRows: ListRow[] = s.specs.slice().reverse().map((sp) => {
    const req = s.requests.find((r) => r.id === sp.request)
    const xr = reviewOf(s, sp.id)
    const rc = checks.find((c) => c.spec_id === sp.id)
    return {
      key: sp.id, kind: 'spec', href: toPath(`work/specs/${sp.id}`), id: sp.id, title: sp.title, from: req?.from,
      state: xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : sp.status,
      size: `요구사항 ${sp.paras.filter((p) => p.kind === 'req').length} · 완료 기준 ${sp.paras.filter((p) => p.kind === 'done').length}`,
      tail: rc ? releaseState(rc) : undefined, note: sp.question ? '질문 대기' : undefined,
    }
  })
  const docRows: ListRow[] = s.docs.slice().reverse().map((x) => {
    const todos = x.blocks.filter((b) => b.kind === 'todo')
    return {
      key: x.id, kind: 'doc', href: toPath(`docs/${x.id}`), id: x.id, title: x.title || '제목 없는 문서', from: s.requests.find((r) => r.id === x.about)?.from,
      state: '문서', size: todos.length ? `할 일 ${todos.filter((b) => b.done).length}/${todos.length}` : `블록 ${x.blocks.length}`, note: x.updated,
    }
  })
  const all = kind === 'spec' ? specRows : kind === 'doc' ? docRows : [...specRows, ...docRows]
  const pg = usePage(all, 10)
  const newDoc = () => { d({ type: 'newDoc', title: '' }); go(toPath(`docs/${nextDocId(s)}`)) }
  const toast = useToast()
  const [notion, setNotion] = useState<string | null>(null) // 노션 주소 칸 (null = 닫힘)
  const importNotion = () => {
    if (!notion?.trim()) return
    d({ type: 'importNotion', url: notion.trim() })
    toast.show('노션 페이지를 가져오는 중')
    setNotion(null)
    setKind('doc')
  }
  return (
    <div className="sl-wrap">
      <div className="sl-top">
        <div className="seg-toggle" role="group" aria-label="종류">
          {([['all', `전체 ${specRows.length + docRows.length}`], ['spec', `기획서 ${specRows.length}`], ['doc', `문서 ${docRows.length}`]] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); pg.setPage(0) }}>{label}</button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        {notion === null ? (
          <Button size="small" onClick={() => setNotion('')}>노션에서 가져오기</Button>
        ) : (
          <form className="sl-notion" onSubmit={(e) => { e.preventDefault(); importNotion() }}>
            <input className="field" autoFocus value={notion} onChange={(e) => setNotion(e.target.value)} placeholder="노션 페이지 주소" aria-label="노션 페이지 주소" />
            <Button size="small" variant="primary" type="submit" disabled={!notion.trim()}>가져오기</Button>
            <Button size="small" onClick={() => setNotion(null)}>취소</Button>
          </form>
        )}
        <Button size="small" variant="primary" onClick={newDoc}>+ 새 문서</Button>
      </div>
      {!pg.rows.length ? <div className="empty"><b>없음</b></div> : (
        <div className="sl">
          <div className="sl-head" aria-hidden><span>상태</span><span>제목</span><span>내용</span><span>배포 전 확인</span></div>
          <ol className="sl-list">
            {pg.rows.map((r) => {
              const nav = rowNav(r.href, `${r.id} 열기`)
              return (
                <li key={r.key} {...nav} className={`sl-item ${nav.className ?? ''}`}>
                  <span>{r.kind === 'spec' ? <State v={r.state} /> : <Tag>문서</Tag>}</span>
                  <span className="sl-title">
                    {r.from && <span className={`rq-dept d-${r.from}`}>{DEPT_NAME[r.from]}</span>}
                    <span className="mono muted sl-id">{r.id}</span>
                    <b title={r.title}>{r.title}</b>
                    {r.kind === 'spec' && r.note && <Tag tone="amber">{r.note}</Tag>}
                  </span>
                  <span className="muted small">{r.size}</span>
                  <span>{r.tail ? <Tag tone={r.tail.tone} title={r.tail.title}>{r.tail.label}</Tag> : <span className="muted small">{r.kind === 'doc' ? r.note : '—'}</span>}</span>
                </li>
              )
            })}
          </ol>
          <Pager {...pg} />
        </div>
      )}
    </div>
  )
}

function SpecView({ s, d, spec, item2 }: P & { spec: Spec; item2?: string }) {
  // 작업·PR·장애가 연결된 문단만 눌러서 오른쪽에 본다. 연결이 없으면 글로만 보인다
  const linked = (id: string) => s.tasks.some((t) => t.spec === spec.id && t.para === id) || s.incidents.some((i) => i.candidates.some((c) => c.spec === spec.id && c.para === id))
  const para = spec.paras.find((p) => p.id === item2 && linked(p.id)) ?? spec.paras.find((p) => linked(p.id))
  const paraHref = (id: string) => toPath(`work/specs/${spec.id}/${id}`)
  const setParaId = (id: string) => go(paraHref(id))
  // 개요 = 목차: 누르면 본문의 그 자리로 (연결된 문단이면 오른쪽도 그 문단으로)
  const jump = (id: string) => setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
  const tasks = para ? s.tasks.filter((t) => t.spec === spec.id && t.para === para.id) : []
  const prs = s.prs.filter((p) => tasks.some((t) => t.id === p.task))
  const decisions = s.decisions.filter((x) => x.spec === spec.id)
  const thread = s.threads.find((t) => t.on === spec.id)
  const inc = para ? s.incidents.find((i) => i.candidates.some((c) => c.spec === spec.id && c.para === para.id)) : undefined
  const xr = reviewOf(s, spec.id)
  const toast = useToast()
  // 노션처럼 칸을 눌러 바로 고치고, 칸에서 벗어나면 저장한다. 확정되면 작업이 나가서 읽기 전용
  // 제목·문제·목표는 언제든, 요구사항·완료 기준은 확정 전까지만 고친다
  const editable = true
  const parasEditable = spec.status !== '확정'
  const [answer, setAnswer] = useState('')
  const save = (patch: { title?: string; problem?: string; goal?: string; paras?: Record<string, string> }) => {
    d({ type: 'editSpec', spec: spec.id, ...patch })
    toast.show(`${spec.id} 저장`)
  }
  const blockOf = (p: S['specs'][number]['paras'][number]) => (
    <SpecPara key={`${p.id}-${p.text}`} p={p} on={p.id === para?.id} onPick={linked(p.id) ? () => setParaId(p.id) : undefined} s={s} spec={spec.id} editable={parasEditable} onSave={(text) => text.trim() !== p.text && save({ paras: { [p.id]: text } })} />
  )

  return (
    <div className="three">
      <aside className="col-left">
        <a href={toPath('work/specs')} className="plain-link small">‹ 문서·기획서 목록</a>
        <div className="ph" style={{ marginTop: 10 }}>{spec.id} 개요</div>
        <ul className="outline">
          <li><button type="button" onClick={() => jump('sec-problem')}>문제 · 목표</button></li>
          {spec.paras.map((p) => (
            <li key={p.id}><button type="button" className={p.id === para?.id ? 'on' : ''} onClick={() => { if (linked(p.id)) setParaId(p.id); jump(`para-${p.id}`) }}>{p.id} {p.kind === 'req' ? '요구사항' : '완료 기준'}</button></li>
          ))}
          {spec.question && <li><button type="button" onClick={() => jump('sec-question')}>질문 1개 남음</button></li>}
          <li><button type="button" onClick={() => jump('sec-decisions')}>결정 이력 {decisions.length}건</button></li>
          {thread && <li><a href={toPath(`talk/${thread.id}`)}>문서 스레드 열기</a></li>}
        </ul>
      </aside>

      <section className="col-doc">
        <input key={`t-${spec.title}`} className="doc-title-input" defaultValue={spec.title} readOnly={!editable} aria-label="기획서 제목"
          onBlur={(e) => editable && e.target.value.trim() && e.target.value.trim() !== spec.title && save({ title: e.target.value })} />
        <div className="doc-head" style={{ marginTop: 4 }}>
          <State v={xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : spec.status} />
          {spec.confirmedBy && <Tag>{CONFIRMED_BY[spec.confirmedBy]}</Tag>}
          {!parasEditable && <Tag title="확정되면 작업이 개발로 나가서 요구사항·완료 기준은 고치지 않아요">문단 잠김</Tag>}
          <span className="muted small">요청 <Ref s={s} id={spec.request} /> · <a href={toPath(`projects/${spec.request}`)}>프로젝트 보기</a> · 사람이 쓴 문장 {spec.humanSentences}개</span>
        </div>
        {spec.status === '초안' && !xr && spec.question && <Notice tone="warn">빈 항목 질문이 남아 있습니다. 답이 오면 AI가 확정 조건(질문 없음·인사 경고 없음·근거 있음)을 다시 보고, 맞으면 바로 확정하고 아니면 AI 교차검증에 올립니다.</Notice>}
        {xr && <ReviewDecide d={d} spec={spec} xr={xr} />}
        {spec.status === '확정' && <ReleaseCheckBox d={d} spec={spec.id} />}
        <div className="doc-blocks">
          <h2 className="spec-h" id="sec-problem">문제</h2>
          <textarea key={`p-${spec.problem}`} className="blk-p" defaultValue={spec.problem} readOnly={!editable} rows={Math.max(1, Math.ceil(spec.problem.length / 70))} aria-label="문제"
            onBlur={(e) => editable && e.target.value.trim() !== spec.problem && save({ problem: e.target.value })} />
          <h2 className="spec-h">목표</h2>
          <textarea key={`g-${spec.goal}`} className="blk-p" defaultValue={spec.goal} readOnly={!editable} rows={Math.max(1, Math.ceil(spec.goal.length / 70))} aria-label="목표"
            onBlur={(e) => editable && e.target.value.trim() !== spec.goal && save({ goal: e.target.value })} />
          <h2 className="spec-h">요구사항</h2>
          {spec.paras.filter((p) => p.kind === 'req').map(blockOf)}
          <h2 className="spec-h">완료 기준</h2>
          {spec.paras.filter((p) => p.kind === 'done').map(blockOf)}
        </div>
        {spec.question && (
          <div className="question" id="sec-question">
            질문 · {spec.question}
            <form className="spec-answer" onSubmit={(e) => { e.preventDefault(); if (!answer.trim()) return; d({ type: 'answerQuestion', spec: spec.id, text: answer.trim() }); setAnswer(''); toast.show(`${spec.id} 질문에 답함`) }}>
              <input className="field" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="답 적기" aria-label="질문에 답" />
              <Button size="small" variant="primary" type="submit" disabled={!answer.trim()}>답하기</Button>
            </form>
          </div>
        )}
        <h3 className="sec">근거</h3>
        <span className="tags">{spec.basis.map((b) => <Tag key={b}>{b}</Tag>)}</span>
      </section>

      <aside className="col-right">
        {para && <>
        <div className="ph">{para.id}에 연결된 작업·PR</div>
        <Sec title="작업" sub={`${tasks.length}건`}>
          {tasks.length ? tasks.map((t) => <div key={t.id} {...rowNav(hrefOf(s, t.id), '이 작업의 PR 보기')}><div className="mini-row"><span className="mono">{t.id}</span> {t.title} <State v={t.state} /></div></div>) : <span className="muted">기획서가 확정되면 split_tasks가 나눈다</span>}
        </Sec>
        <Sec title="PR" sub="작업 1개 = PR 1개">
          {prs.length ? prs.map((p) => <PrLine key={p.id} s={s} p={p} d={d} />) : <span className="muted">아직 없음</span>}
        </Sec>
        </>}
        <div id="sec-decisions" />
        <Sec title="스레드 · 결정" sub={thread ? <a href={toPath(`talk/${thread.id}`)}>스레드 전체 보기</a> : undefined}>
          {thread?.messages.slice(-2).map((m, i) => <div key={i} className="msg"><b>{m.who}</b> <Linked s={s} text={m.text} />{m.decision && <> <Tag tone="green">{m.decision}</Tag></>}</div>)}
          {decisions.map((x) => <div key={x.id} className="msg"><span className="mono">{x.id}</span> {x.text} <HrRecord d={x} /></div>)}
        </Sec>
        {para && <Sec title="장애 연결" sub="DejaFix 사후관리">
          {inc ? <div className="msg"><Ref s={s} id={inc.ctx} /> {inc.title} → 관련 PR {inc.candidates.map((c, i) => <span key={c.pr}>{i > 0 && ', '}<Ref s={s} id={c.pr} /></span>)}</div> : <span className="muted">연결된 사건 없음</span>}
        </Sec>}
        <div className="ph" style={{ marginTop: 6 }}>최근 에이전트 활동</div>
        <ActivityList s={s} items={s.activity.filter((a) => a.ctx === (s.requests.find((r) => r.id === spec.request)?.ctx ?? 'ctx_req_0012')).slice(-4).reverse()} compact />
      </aside>
    </div>
  )
}

// 기획서 문단 한 줄 (노션 블록처럼): 번호 + 바로 고치는 글 칸 + 경고·연결 칩. 연결된 작업이 있으면 번호를 눌러 오른쪽에 본다
function SpecPara({ p, on, onPick, s, spec, editable, onSave }: { p: S['specs'][number]['paras'][number]; on: boolean; onPick?: () => void; s: S; spec: string; editable: boolean; onSave: (text: string) => void }) {
  const tasks = s.tasks.filter((t) => t.spec === spec && t.para === p.id)
  const prs = s.prs.filter((x) => tasks.some((t) => t.id === x.task))
  const done = tasks.length > 0 && tasks.every((t) => t.state === '완료')
  return (
    <div id={`para-${p.id}`} className={`spec-blk${on ? ' on' : ''}`}>
      <div className="blk">
        {onPick ? <button type="button" className="pid spec-pid" onClick={onPick} title={`${p.id}에 연결된 작업·PR 보기`}>{p.id}</button> : <span className="pid spec-pid">{p.id}</span>}
        <textarea className="blk-p" defaultValue={p.text} readOnly={!editable} rows={Math.max(1, Math.ceil(p.text.length / 70))} aria-label={`${p.id} 내용`}
          onBlur={(e) => editable && onSave(e.target.value)} />
      </div>
      {p.warn && <div className="warnline">인사총무 ② 경고 ({p.warn.card}) · {p.warn.text}</div>}
      {p.incident && <div className="incline">장애 표시 · {p.incident} (link_incident)</div>}
      {(tasks.length > 0 || done) && (
        <span className="tags spec-tags">
          {tasks.map((t) => <Tag key={t.id}>{t.id}</Tag>)}
          {prs.map((x) => <Tag key={x.id}>{x.id}</Tag>)}
          {done && <Tag tone="green">구현됨</Tag>}
        </span>
      )}
    </div>
  )
}

// PR 한 줄 (기획서 옆 칸). 머지 버튼은 없다: 기준 안이면 자동 머지, 밖이면 교차검증, 되돌릴 수 없는 일이면 사람 확인
function PrLine({ s, p, d }: { s: S; p: PR; d: Dispatch<Action> }) {
  const last = p.reviews[p.reviews.length - 1]
  const xr = reviewOf(s, p.id)
  const rejected = !p.merged && !xr && s.reviews.some((x) => x.ref === p.id && x.result === '거절')
  return (
    <div className="pr-line">
      <div><a href={toPath(`work/prs/${prSlug(p.id)}`)}><b>{p.id}</b> {p.title}</a></div>
      <div className="muted small mono">{p.gate.lines}줄·{p.gate.files}파일 · {p.gate.paths}</div>
      <span className="tags">
        <Tag tone={last === '통과' ? 'green' : 'amber'}>검수 {last}{p.reviews.length > 1 ? ` (${p.reviews.length}회차)` : ''}</Tag>
        <RepoTag p={p} />
        {p.merged ? <Tag tone="green">{mergedLabel(p)}</Tag> : xr ? <a href={toPath(`work/reviews/${xr.id}`)}><Tag tone="amber">{xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기'} {xr.id}</Tag></a> : rejected ? <Tag tone="amber">검증자 거절</Tag> : null}
      </span>
      {!p.merged && (last === '불일치' || rejected) && <Button size="small" onClick={() => d({ type: 'reviewPr', pr: p.id })}>다시 검수</Button>}
    </div>
  )
}

// 결정의 인사 기록 결과: 실제로 보냈으면 결과(기록됨·실패·보류), 목 모드면 예전처럼 "전송"
const HR_RECORD: Record<NonNullable<Decision['hrRecord']>, { label: string; tone?: 'green' | 'amber' }> = {
  ok: { label: '인사 기록됨', tone: 'green' },
  failed: { label: '인사 기록 실패', tone: 'amber' },
  held: { label: '인사 보류', tone: 'amber' },
}
function HrRecord({ d }: { d: Decision }) {
  if (d.hrRecord) return <Tag tone={HR_RECORD[d.hrRecord].tone}>{HR_RECORD[d.hrRecord].label}</Tag>
  return d.sentToHr ? <span className="muted">· 인사에 ① 전송</span> : null
}
