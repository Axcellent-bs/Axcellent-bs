// 기획서 화면: 문서가 원본이고 문단에 작업·PR·결정이 붙는다
import { useEffect, useState, type Dispatch } from 'react'
import { Button, Notice, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, exampleFill, nextDocId, type Action, type FillPreview, type Decision, type Spec, type State as S } from '@/lib/planningData'
import { State, Tag, rowNav } from '@/components/planning-common/parts'
import { Linked, Ref, go, reviewOf, toPath } from '@/components/planning-common/links'
import { CONFIRMED_BY } from '@/components/planning-common/flow'
import { Pager, usePage } from '@/components/planning-common/pager'
import { Markdown } from '@/components/planning-common/markdown'
import { PROPOSAL_ORIGIN, handoffFailed, proposalReady, type Proposal } from '@/lib/proposals'
import { TEMPLATES, TEMPLATE_NAME, templateOf, type Section } from '@/lib/specTemplate'
import { notionApi, previewFill, previewSuggest, useAllReleaseChecks, useReleaseChecks, type NotionHit, type ReleaseCheck } from '@/lib/planning'

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
export function Specs({ s, d, item, item2, example }: P & { item?: string; item2?: string; example?: boolean }) {
  if (!item) return <SpecList s={s} d={d} />
  const spec = s.specs.find((x) => x.id === item)
  if (!spec) return <div className="empty"><b>{item} 없음</b> <a href={toPath('work/specs')}>기획서 목록</a></div>
  return <SpecView key={spec.id} s={s} d={d} spec={spec} item2={item2} example={example} />
}

type ListKind = 'all' | 'spec' | 'doc'
type ListRow = { key: string; kind: 'spec' | 'plan' | 'doc'; href: string; id: string; title: string; from?: S['requests'][number]['from']; to?: 'marketing' | 'dev'; state: string; size: string; tail?: { label: string; tone: 'green' | 'amber'; title?: string }; note?: string; origin?: '요청' | '제안' }
// 문서·기획서 목록 (탭 첫 화면). 기획서(요청에서 나온 것)와 사람이 쓴 문서를 한 목록에, 최근 것이 위
function SpecList({ s, d }: { s: S; d: Dispatch<Action> }) {
  const checks = useAllReleaseChecks()
  const [kind, setKind] = useState<ListKind>('all')
  const specRows: ListRow[] = s.specs.slice().reverse().map((sp) => {
    const req = s.requests.find((r) => r.id === sp.request)
    const xr = reviewOf(s, sp.id)
    const rc = checks.find((c) => c.spec_id === sp.id)
    // 어디서 나온 기획서인지: 기획 제안 → 기획안 → 개발로 넘긴 요청이면 "제안", 나머지는 "요청"
    const prop = (s.proposals ?? []).find((p) => p.spec === sp.id || (!!p.request && p.request === req?.id))
    const fromProposal = sp.origin === PROPOSAL_ORIGIN || !!prop
    // 요청은 보낸 부서, 제안은 넘긴 부서 (넘기기 전·전달 실패면 칩 없음)
    const to = !prop ? undefined : prop.status === '기획서로' ? 'dev' as const
      : ['마케팅 전달', '진행 중', '성과 확인'].includes(prop.status) && !handoffFailed(prop) ? 'marketing' as const : undefined
    return {
      origin: fromProposal ? '제안' : '요청', to: fromProposal ? to : undefined,
      key: sp.id, kind: 'spec', href: toPath(`work/specs/${sp.id}`), id: sp.id, title: sp.title, from: fromProposal ? undefined : req?.from,
      state: xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : sp.origin === PROPOSAL_ORIGIN && sp.status === '초안' ? '기획안' : sp.status,
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
  // 작성일 최신순: 문서는 만든 시각(예전 문서는 고친 시각), 기획서는 그 기획서가 처음 나온 활동 시각 ("MM-DD HH:mm")
  const firstAct = (id: string) => s.activity.reduce((m, x) => (x.text.includes(id) && x.t && (!m || x.t < m) ? x.t : m), '')
  const timeOf = (r: ListRow) => {
    if (r.kind !== 'spec') { const x = s.docs.find((y) => y.id === r.id); return x?.created || firstAct(r.id) || x?.updated || '' }
    return firstAct(r.id)
  }
  const num = (id: string) => Number(id.split('-')[1]) || 0
  const newest = (xs: ListRow[]) => xs.map((r) => ({ r, t: timeOf(r) })).sort((a, b) => (a.t === b.t ? num(b.r.id) - num(a.r.id) : a.t < b.t ? 1 : -1)).map((x) => x.r)
  const planRows = docRows.filter((r) => r.kind === 'plan')
  const plainDocs = docRows.filter((r) => r.kind === 'doc')
  const all = newest(kind === 'spec' ? [...specRows, ...planRows] : kind === 'doc' ? plainDocs : [...specRows, ...docRows])
  const pg = usePage(all, 10)
  const newDoc = () => { d({ type: 'newDoc', title: '' }); go(toPath(`docs/${nextDocId(s)}`)) }
  const toast = useToast()
  const [notion, setNotion] = useState<string | null>(null) // 노션 주소·제목 칸 (null = 닫힘)
  const [hits, setHits] = useState<NotionHit[] | null>(null) // 제목 검색 결과
  const [searching, setSearching] = useState(false)
  const [notionErr, setNotionErr] = useState<string | null>(null)
  const closeNotion = () => { setNotion(null); setHits(null); setNotionErr(null) }
  const importPage = (url: string, title?: string) => {
    d({ type: 'importNotion', url })
    toast.show(title ? `"${title}" 가져오는 중` : '노션 페이지를 가져오는 중')
    closeNotion()
    setKind('doc')
  }
  // 주소·페이지 ID면 바로 가져오고, 아니면 제목으로 찾아 고르게 한다 (연결 › 문서 › 노션의 토큰을 쓴다)
  const isLink = (x: string) => /notion\.(so|site)\//i.test(x) || /[0-9a-f]{32}/i.test(x.replace(/-/g, ''))
  const importNotion = async () => {
    const q = notion?.trim()
    if (!q) return
    if (isLink(q)) return importPage(q)
    setSearching(true)
    setNotionErr(null)
    try { setHits(await notionApi.search(q)) } catch (x) { setHits(null); setNotionErr((x as Error).message) } finally { setSearching(false) }
  }
  return (
    <div className="sl-wrap">
      <div className="sl-top">
        <div className="seg-toggle" role="group" aria-label="종류">
          {([['all', `전체 ${specRows.length + docRows.length}`], ['spec', `기획서 ${specRows.length + planRows.length}`], ['doc', `문서 ${plainDocs.length}`]] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); pg.setPage(0) }}>{label}</button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        {notion === null ? (
          <Button size="small" onClick={() => setNotion('')}>노션에서 가져오기</Button>
        ) : (
          <form className="sl-notion" onSubmit={(e) => { e.preventDefault(); void importNotion() }}>
            <input className="field" autoFocus value={notion} onChange={(e) => { setNotion(e.target.value); setHits(null) }} placeholder="노션 주소 또는 페이지 제목" aria-label="노션 주소 또는 페이지 제목" />
            <Button size="small" variant="primary" type="submit" disabled={!notion.trim() || searching}>{searching ? '찾는 중…' : isLink(notion) ? '가져오기' : '찾기'}</Button>
            <Button size="small" onClick={closeNotion}>취소</Button>
          </form>
        )}
        <Button size="small" variant="primary" onClick={newDoc}>+ 새 문서</Button>
      </div>
      {notion !== null && (notionErr || hits) && (
        <div className="sl-notion-hits">
          {notionErr ? <span className="small" style={{ color: 'var(--danger, #b91c1c)' }}>{notionErr}</span>
            : !hits?.length ? <span className="muted small">"{notion}" 제목의 페이지가 없어요. 노션에서 그 페이지에 통합을 연결했는지 확인해 주세요.</span>
            : (
              <ol>
                {hits.map((h) => (
                  <li key={h.id}>
                    <b>{h.title}</b>
                    <span className="muted small">{h.edited ? new Date(h.edited).toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' }) + ' 수정' : ''}</span>
                    <span style={{ flex: 1 }} />
                    <a className="small" href={h.url} target="_blank" rel="noreferrer">노션에서 보기 ↗</a>
                    <Button size="small" variant="primary" onClick={() => importPage(h.id, h.title)}>가져오기</Button>
                  </li>
                ))}
              </ol>
            )}
        </div>
      )}
      {!pg.rows.length ? <div className="empty"><b>없음</b></div> : (
        <div className="sl">
          <div className="sl-head" aria-hidden><span>상태</span><span>제목</span><span>내용</span><span>작성일</span></div>
          <ol className="sl-list">
            {pg.rows.map((r) => {
              const nav = rowNav(r.href, `${r.id} 열기`)
              return (
                <li key={r.key} {...nav} className={`sl-item ${nav.className ?? ''}`}>
                  <span>{r.kind === 'doc' ? <Tag>문서</Tag> : <State v={r.state} />}</span>
                  <span className="sl-title">
                    {r.origin && <span className={`sl-origin${r.origin === '제안' ? ' prop' : ''}`} title={r.origin === '제안' ? '기획 제안에서 만든 기획서' : '요청에서 만든 기획서'}>{r.origin}</span>}
                    {r.from && <span className={`rq-dept d-${r.from}`} title="요청한 부서">{DEPT_NAME[r.from]}</span>}
                    {r.to && <span className={`rq-dept d-${r.to}`} title="기획안을 넘긴 부서">→ {DEPT_NAME[r.to]}</span>}
                    <span className="mono muted sl-id">{r.id}</span>
                    <b title={r.title}>{r.title}</b>
                    {r.kind === 'spec' && r.note && <Tag tone="amber">{r.note}</Tag>}
                    {r.tail && <Tag tone={r.tail.tone} title={r.tail.title}>{r.tail.label}</Tag>}
                  </span>
                  <span className="muted small">{r.size}</span>
                  <span className="muted small">{timeOf(r) || '—'}</span>
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

// 기획 제안에서 만든 기획안(origin proposal) 위의 넘기기 줄: 자동 확정 없이 사람이 마케팅·개발 중 고른다.
// 숏츠·배너 같은 제작물이면 마케팅을, 아니면 개발을 파란 버튼으로. 넘긴 뒤에는 어디로 갔는지만 보인다.
const MARKETING_FORMATS = ['숏폼', '숏츠', '배너', '공지', 'SNS']
const marketingFirst = (p: Proposal) => !!p.shortsFormat || [p.format].some((x) => x && MARKETING_FORMATS.some((k) => x.includes(k)))
function PlanHandoff({ s, d, spec }: P & { spec: Spec }) {
  const toast = useToast()
  const p = (s.proposals ?? []).find((x) => x.spec === spec.id)
  if (spec.origin !== PROPOSAL_ORIGIN || !p) return null
  const flush = () => (document.activeElement as HTMLElement | null)?.blur()
  const mk = marketingFirst(p)
  return (
    <div className="plan-handoff">
      <span className="sl-origin prop">제안</span>
      <a className="plain-link mono small" href={toPath(`proposals/${p.id}`)}>{p.id}</a>
      {p.target && <span className="small">목표 <b>{p.target.value}</b> {p.target.label}</span>}
      <span style={{ flex: 1 }} />
      {proposalReady(p) ? (
        <>
          {p.handoff && !p.handoff.ok
            ? <span className="plan-fail" title={p.handoff.reason}>마케팅 전달 실패{p.handoff.reason ? ` · ${p.handoff.reason}` : ''}</span>
            : <span className="muted small">이 기획안을</span>}
          <Button size="small" variant={mk ? 'primary' : undefined} onClick={() => { flush(); d({ type: 'approveProposal', proposal: p.id }); toast.show(`${spec.id} → 마케팅`) }}>{p.handoff && !p.handoff.ok ? '마케팅에 다시 보내기' : '마케팅으로 넘기기'}</Button>
          <Button size="small" variant={mk ? undefined : 'primary'} onClick={() => { flush(); d({ type: 'proposalToRequest', proposal: p.id }); toast.show(`${spec.id} → 개발`) }}>개발로 넘기기</Button>
        </>
      ) : p.status === '기획서로' ? <span className="small">개발로 넘김 · 작업 {s.tasks.filter((t) => t.spec === spec.id).length}개</span>
        : p.handoff ? <span className="small">마케팅 {p.handoff.ok ? `받음${p.handoff.campaign ? ` · ${p.handoff.campaign}` : ''}` : '전달 실패'} · <a href={toPath(`proposals/${p.id}`)}>{p.status}</a></span>
        : <span className="muted small">{p.status}</span>}
    </div>
  )
}

// 양옆 칸 접기·펴기 (브라우저에 기억). 접으면 좁은 띠만 남고 누르면 다시 펼친다
function useStoredOpen(key: string): [boolean, (v: boolean) => void] {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== '0' } catch { return true } })
  const set = (v: boolean) => { setOpen(v); try { localStorage.setItem(key, v ? '1' : '0') } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ } }
  return [open, set]
}

function PaneToggle({ side, open, onToggle }: { side: 'left' | 'right'; open: boolean; onToggle: () => void }) {
  const arrow = (side === 'left') === open ? '‹' : '›'
  return <button type="button" className={`pane-toggle ${side}`} onClick={onToggle} aria-expanded={open} aria-label={open ? '칸 접기' : '칸 펼치기'} title={open ? '접기' : '펼치기'}>{arrow}</button>
}

function Rail({ side, label, onOpen }: { side: 'left' | 'right'; label: string; onOpen: () => void }) {
  return (
    <aside className={`pane-rail ${side}`}>
      <button type="button" className="pane-rail-btn" onClick={onOpen} aria-expanded={false} aria-label={`${label} 펼치기`} title={`${label} 펼치기`}>
        <span className="pane-rail-arrow" aria-hidden>{side === 'left' ? '›' : '‹'}</span>
      </button>
    </aside>
  )
}

// 가운데 자유 작성 캔버스. 보기 모드는 서식대로만 보이고, "수정" 중에만 원문(마크다운)을 고친다 (편집·미리보기)
const NL = String.fromCharCode(10)
const FREE_HINT = ['배경, 목표, 해야 할 일, 다 됐는지 확인할 방법을 자유롭게 써 주세요.', '다 쓰고 왼쪽 ✦ 자동 채우기를 누르면 AI가 읽고 필수 항목을 채워요.', '', '## 배경', '## 목표', '## 요구사항', '- ', '## 완료 기준', '- '].join(NL)

function FreeCanvas({ value, editing, onChange }: { value: string; editing: boolean; onChange: (text: string) => void }) {
  const [preview, setPreview] = useState(false)
  const showEdit = editing && !preview
  return (
    <section className="fc" id="sec-free" aria-label="자유 작성">
      <div className="fc-bar">
        <h2>자유 작성</h2>
        <span style={{ flex: 1 }} />
        {editing && (
          <div className="fc-toggle" role="group" aria-label="보기 방식">
            <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)}>편집</button>
            <button type="button" aria-pressed={preview} disabled={!value.trim()} onClick={() => setPreview(true)}>미리보기</button>
          </div>
        )}
      </div>
      {showEdit ? (
        <textarea className="fc-edit" autoFocus value={value} placeholder={FREE_HINT} aria-label="자유 작성 (마크다운)" onChange={(e) => onChange(e.target.value)} />
      ) : value.trim() ? (
        <div className="fc-view"><Markdown text={value} /></div>
      ) : (
        <p className="fc-empty">아직 비어 있어요. 위의 <b>수정</b>을 눌러 자유롭게 써 주세요.</p>
      )}
    </section>
  )
}

// 수정 이력 (구글 문서의 "버전 기록"처럼): 이 기획서에 남은 사람·AI 변경을 날짜별로. 되돌리기는 다음 과제
const HISTORY_POINTS = new Set(['write_spec', 'edit_spec', 'fill_spec', 'answer_question', 'extract_decision', 'confirm_spec', 'auto_review', 'reject_spec', 'make_plan', 'delete_spec', 'spec_question', 'receive_planning_answer', 'split_tasks', 'start_dev'])
function History({ s, spec, onClose }: { s: S; spec: Spec; onClose: () => void }) {
  const ctx = s.requests.find((r) => r.id === spec.request)?.ctx
  const items = s.activity
    .filter((a) => (a.ctx === ctx || a.text.includes(spec.id)) && (HISTORY_POINTS.has(a.point) || a.text.includes(spec.id)))
    .slice()
    .reverse()
  const days = new Map<string, typeof items>()
  for (const a of items) {
    const day = a.t.slice(0, 5)
    days.set(day, [...(days.get(day) ?? []), a])
  }
  const who = (a: S['activity'][number]) => (a.kind === '사람' || a.from === 'human' ? '사람' : DEPT_NAME[a.from] ?? a.from)
  return (
    <aside className="col-right hist" aria-label="수정 이력">
      <header className="hist-head">
        <h2>수정 이력</h2>
        <button type="button" className="hist-x" onClick={onClose} aria-label="수정 이력 닫기">✕</button>
      </header>
      {!items.length ? <p className="muted small">아직 기록이 없어요.</p> : (
        <ol className="hist-list">
          {[...days.entries()].map(([day, list]) => (
            <li key={day}>
              <h3 className="hist-day">{day}</h3>
              <ol>
                {list.map((a, i) => (
                  <li key={i} className={`hist-item${who(a) === '사람' ? ' human' : ''}`}>
                    <span className="hist-time">{a.t.slice(6)}</span>
                    <span className="hist-who">{who(a)}</span>
                    <p className="hist-text"><Linked s={s} text={a.text} /></p>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      )}
      <p className="hist-note">되돌리기는 준비 중이에요.</p>
    </aside>
  )
}

type Draft = { title: string; problem: string; goal: string; reqs: string[]; dones: string[]; bodyMd: string; extra: Record<string, string> }
const draftOf = (spec: Spec): Draft => ({
  title: spec.title, problem: spec.problem, goal: spec.goal,
  reqs: spec.paras.filter((p) => p.kind === 'req').map((p) => p.text),
  dones: spec.paras.filter((p) => p.kind === 'done').map((p) => p.text),
  bodyMd: spec.bodyMd ?? '',
  extra: { ...(spec.extra ?? {}) },
})
const sameExtra = (a: Record<string, string>, b: Record<string, string>) => [...new Set([...Object.keys(a), ...Object.keys(b)])].every((k) => (a[k] ?? '').trim() === (b[k] ?? '').trim())
const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x.trim() === b[i].trim())

// 수정 중 목록 칸 (요구사항·완료 기준): 줄마다 고치고, 추가·삭제
function ListEdit({ prefix, items, onChange, locked }: { prefix: string; items: string[]; onChange: (xs: string[]) => void; locked: boolean }) {
  if (locked) return <>{items.map((t, i) => <p key={i} className="sp-static"><span className="sp-pid">{prefix}{i + 1}</span>{t}</p>)}</>
  return (
    <>
      {items.map((t, i) => (
        <div key={i} className="sp-row">
          <span className="sp-pid">{prefix}{i + 1}</span>
          <textarea className="sp-input" value={t} placeholder="비어 있음" aria-label={`${prefix}${i + 1}`} onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
          <button type="button" className="sp-del" aria-label={`${prefix}${i + 1} 지우기`} onClick={() => onChange(items.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {items.length < 6 && <button type="button" className="sp-add" onClick={() => onChange([...items, ''])}>+ 추가</button>}
    </>
  )
}

function SpecView({ s, d, spec, item2 }: P & { spec: Spec; item2?: string; example?: boolean }) {
  // 작업·PR·장애가 연결된 문단만 눌러서 오른쪽에 본다
  const linked = (id: string) => s.tasks.some((t) => t.spec === spec.id && t.para === id) || s.incidents.some((i) => i.candidates.some((c) => c.spec === spec.id && c.para === id))
  const para = spec.paras.find((p) => p.id === item2 && linked(p.id)) ?? spec.paras.find((p) => linked(p.id))
  const setParaId = (id: string) => go(toPath(`work/specs/${spec.id}/${id}`))
  const jump = (id: string) => setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
  const decisions = s.decisions.filter((x) => x.spec === spec.id)
  const thread = s.threads.find((t) => t.on === spec.id)
  const xr = reviewOf(s, spec.id)
  const toast = useToast()
  const locked = spec.status === '확정' // 확정되면 요구사항·완료 기준은 작업이 나가 잠긴다
  const tpl = templateOf(spec.origin) // 기획 제안에서 온 기획서는 제안서 양식 (배경·타깃·핵심 메시지·성공 지표…)
  const sections = TEMPLATES[tpl]
  const [leftOpen, setLeftOpen] = useStoredOpen('ax.spec.left')
  const [historyOpen, setHistoryOpen] = useState(false)

  // "수정" → 화면 초안에서만 고치고 "저장"할 때 한 번에 보낸다. "취소"는 초안을 버린다
  const [draft, setDraft] = useState<Draft | null>(null)
  const [answerFound, setAnswerFound] = useState('') // 자동 채우기가 자유 작성에서 찾은 질문 답 (저장하면 답으로 보냄)
  const [filling, setFilling] = useState(false)
  const editing = draft !== null
  const cur = draft ?? draftOf(spec)
  const orig = draftOf(spec)
  const dirty = editing && (cur.title !== orig.title || cur.problem !== orig.problem || cur.goal !== orig.goal || cur.bodyMd !== orig.bodyMd || !sameList(cur.reqs, orig.reqs) || !sameList(cur.dones, orig.dones) || !sameExtra(cur.extra, orig.extra) || !!answerFound)
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const set = (patch: Partial<Draft>) => setDraft({ ...cur, ...patch })
  const startEdit = () => { setDraft(draftOf(spec)); setAnswerFound('') }
  const cancel = () => {
    if (dirty && !window.confirm('고친 내용을 버릴까요?')) return
    setDraft(null); setAnswerFound('')
  }
  const saveAll = () => {
    if (!draft) return
    const patch: Record<string, unknown> = {}
    if (cur.title.trim() && cur.title !== orig.title) patch.title = cur.title
    if (cur.problem.trim() && cur.problem !== orig.problem) patch.problem = cur.problem
    if (cur.goal.trim() && cur.goal !== orig.goal) patch.goal = cur.goal
    if (cur.bodyMd !== orig.bodyMd) patch.body_md = cur.bodyMd
    if (!sameExtra(cur.extra, orig.extra)) patch.extra = Object.fromEntries(sections.filter((x) => x.source === 'extra').map((x) => [x.key, cur.extra[x.key] ?? '']))
    if (!locked && !sameList(cur.reqs, orig.reqs)) patch.reqs = cur.reqs.filter((x) => x.trim())
    if (!locked && !sameList(cur.dones, orig.dones)) patch.dones = cur.dones.filter((x) => x.trim())
    if (Object.keys(patch).length) d({ type: 'editSpec', spec: spec.id, ...patch } as Action)
    if (answerFound && spec.question) d({ type: 'answerQuestion', spec: spec.id, text: answerFound })
    setDraft(null); setAnswerFound('')
    toast.show(Object.keys(patch).length || answerFound ? `${spec.id} 저장됨` : '바뀐 내용이 없어요')
  }

  // ✦ 자동 채우기: AI가 (저장 안 된) 자유 작성을 읽고 채운 값을 초안에만 넣는다. 저장해야 반영된다
  // 누르면 늘 AI 값으로 바꾼다 (빈 칸이 아니어도). 초안에만 들어가서 "취소"로 되돌릴 수 있다
  const autoFill = async () => {
    const base = draft ?? draftOf(spec)
    if (!base.bodyMd.trim()) { toast.show('자유 작성을 먼저 써 주세요'); return }
    if (!draft) setDraft(base)
    setFilling(true)
    try {
      // 예시 모드여도 서버의 기획 AI가 읽는다. 서버에 닿지 않을 때만 소제목 읽기로 대신한다
      let got: FillPreview & { ai: boolean }
      try {
        got = await previewFill({ spec: spec.id, template: tpl, title: base.title, bodyMd: base.bodyMd, question: spec.question, current: { problem: base.problem, goal: base.goal, requirements: base.reqs, done_criteria: base.dones } })
      } catch {
        got = { ...exampleFill(base.bodyMd), ai: false }
      }
      const next = { ...base }
      const filled: string[] = []
      const label = (src: string) => sections.find((x) => x.source === src || x.key === src)?.label ?? src
      if (got.problem && got.problem !== base.problem) { next.problem = got.problem; filled.push(label('problem')) }
      if (got.goal && got.goal !== base.goal) { next.goal = got.goal; filled.push(label('goal')) }
      if (!locked && got.reqs.length && !sameList(got.reqs, base.reqs)) { next.reqs = got.reqs; filled.push(label('req')) }
      if (!locked && got.done.length && !sameList(got.done, base.dones)) { next.dones = got.done; filled.push(label('done')) }
      next.extra = { ...base.extra }
      for (const x of sections.filter((y) => y.source === 'extra')) {
        const v = got.extra?.[x.key]
        if (v && v !== base.extra[x.key]) { next.extra[x.key] = v; filled.push(x.label) }
      }
      setDraft(next)
      void askSuggest(next) // 자동 채우기 뒤에도 빈 칸이 남으면 AI 제안
      if (got.answer && spec.question) { setAnswerFound(got.answer); filled.push('질문 답') }
      const how = !got.ai ? 'AI 연결 안 됨 · 소제목만 읽었어요' : got.by === 'llm' ? 'AI가 읽고 채웠어요' : 'AI가 채우고 빈 곳은 소제목으로'
      toast.show(filled.length ? `${how} · ${filled.join(', ')} · 확인하고 저장해 주세요` : locked ? '바뀐 항목이 없어요 (확정된 기획서라 요구사항·완료 기준은 잠겨 있어요)' : '자유 작성에서 바꿀 내용을 찾지 못했어요')
    } catch (x) {
      toast.show(`자동 채우기 실패: ${(x as Error).message}`)
    } finally {
      setFilling(false)
    }
  }

  // AI 제안: 비어 있는 필수 칸에 무엇을 쓸지 AI가 안을 낸다 (열 때, 자동 채우기 뒤에도 비어 있으면). 고르면 초안에만 들어간다
  const FIELD: Record<string, string> = { req: 'requirements', done: 'done_criteria', problem: 'problem', goal: 'goal' }
  const [sugg, setSugg] = useState<Record<string, string | string[]>>({})
  const [suggesting, setSuggesting] = useState(false)
  const emptyOf = (b: Draft) => sections.filter((x) => {
    if (x.source === 'req' || x.source === 'done') return !locked && !(x.source === 'req' ? b.reqs : b.dones).some((t) => t.trim())
    return !(x.source === 'extra' ? b.extra[x.key] ?? '' : b[x.source]).trim()
  })
  const askSuggest = async (b: Draft) => {
    const empty = emptyOf(b)
    if (!empty.length) { setSugg({}); return }
    setSuggesting(true)
    try {
      const got = await previewSuggest({
        spec: spec.id, template: tpl, title: b.title, bodyMd: b.bodyMd, basis: spec.basis ?? [],
        fields: empty.map((x) => (x.source === 'extra' ? x.key : FIELD[x.source])),
        current: { problem: b.problem, goal: b.goal, requirements: b.reqs, done_criteria: b.dones, ...b.extra },
      })
      const out: Record<string, string | string[]> = {}
      for (const x of empty) {
        const v = x.source === 'req' ? got.reqs : x.source === 'done' ? got.done : x.source === 'extra' ? got.extra?.[x.key] ?? '' : got[x.source]
        if (Array.isArray(v) ? v.length : v.trim()) out[x.key] = v
      }
      setSugg(out)
    } catch {
      setSugg({})
    } finally {
      setSuggesting(false)
    }
  }
  useEffect(() => { setSugg({}); if (!draft) void askSuggest(draftOf(spec)) }, [spec.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const valueOf = (x: Section, v: string | string[]): Partial<Draft> => (
    x.source === 'req' ? { reqs: v as string[] } : x.source === 'done' ? { dones: v as string[] }
      : x.source === 'extra' ? { extra: { ...cur.extra, [x.key]: v as string } } : { [x.source]: v as string }
  )
  const applySugg = (x: Section) => {
    const v = sugg[x.key]; if (!v) return
    set(valueOf(x, v)); setSugg(({ [x.key]: _, ...rest }) => rest)
  }
  const applyAll = () => {
    let next = { ...cur, extra: { ...cur.extra } }
    for (const x of sections) {
      const v = sugg[x.key]; if (!v) continue
      next = x.source === 'extra' ? { ...next, extra: { ...next.extra, [x.key]: v as string } } : { ...next, ...valueOf(x, v), extra: next.extra }
    }
    setDraft(next); setSugg({})
  }
  const suggCount = Object.keys(sugg).length
  const suggEl = (x: Section, empty: boolean) => {
    const v = sugg[x.key]
    if (!empty || !v) return null
    return (
      <div className="sp-sugg">
        <span className="sp-sugg-tag">AI 제안</span>
        {Array.isArray(v) ? <ul>{v.map((t, i) => <li key={i}>{t}</li>)}</ul> : <p>{v}</p>}
        <div className="sp-sugg-act">
          <button type="button" onClick={() => applySugg(x)}>적용</button>
          <button type="button" className="quiet" onClick={() => setSugg(({ [x.key]: _, ...rest }) => rest)}>닫기</button>
        </div>
      </div>
    )
  }

  const deletable = !locked && !s.tasks.some((t) => t.spec === spec.id)
  const removeSpec = () => {
    if (!window.confirm(`${spec.id} "${spec.title}" 기획서를 지울까요? 요청은 남고 접수 상태로 돌아가요.`)) return
    d({ type: 'deleteSpec', spec: spec.id })
    toast.show(`${spec.id} 삭제`)
    go(toPath('work/specs'))
  }
  const [answer, setAnswer] = useState('')
  const hasFree = !!cur.bodyMd.trim()
  const reqParas = spec.paras.filter((p) => p.kind === 'req')
  const doneParas = spec.paras.filter((p) => p.kind === 'done')
  const blockOf = (p: S['specs'][number]['paras'][number]) => (
    <SpecPara key={`${p.id}-${p.text}`} p={p} on={p.id === para?.id} onPick={linked(p.id) ? () => setParaId(p.id) : undefined} s={s} spec={spec.id} editable={false} onSave={() => undefined} />
  )
  const lockTag = locked && <span className="sp-lock" title="확정되면 작업이 개발로 나가서 고치지 않아요">잠김</span>
  const sectionEl = (x: Section) => {
    if (x.source === 'req' || x.source === 'done') {
      const isReq = x.source === 'req'
      const paras = isReq ? reqParas : doneParas
      return (
        <div key={x.key} className="sp-prop sp-list">
          <span className="sp-label">{x.label}{lockTag}</span>
          {editing ? <ListEdit prefix={isReq ? 'R' : 'C'} items={isReq ? cur.reqs : cur.dones} locked={locked} onChange={(xs) => set(isReq ? { reqs: xs } : { dones: xs })} />
            : paras.length ? paras.map(blockOf) : <span className="sp-empty">비어 있음</span>}
          {suggEl(x, !(isReq ? cur.reqs : cur.dones).some((t) => t.trim()))}
        </div>
      )
    }
    const value = x.source === 'extra' ? cur.extra[x.key] ?? '' : cur[x.source]
    const onChange = (v: string) => (x.source === 'extra' ? set({ extra: { ...cur.extra, [x.key]: v } }) : set({ [x.source]: v }))
    return (
      <div key={x.key} className="sp-prop" id={x.source === 'problem' ? 'sec-problem' : undefined}>
        <label htmlFor={`sp-${x.key}-${spec.id}`}>{x.label}</label>
        {editing
          ? <textarea id={`sp-${x.key}-${spec.id}`} className="sp-input" value={value} placeholder={x.placeholder || '비어 있음'} onChange={(e) => onChange(e.target.value)} />
          : value.trim() ? <p className="sp-static">{value}</p> : <span className="sp-empty">비어 있음</span>}
        {suggEl(x, !value.trim())}
      </div>
    )
  }

  return (
    <div className={`three spec-three${leftOpen ? '' : ' no-left'}${historyOpen ? ' with-hist' : ''}${editing ? ' is-editing' : ''}`}>
      {!leftOpen ? <Rail side="left" label="필수 항목" onOpen={() => setLeftOpen(true)} /> : <aside className="col-left spec-left">
        <PaneToggle side="left" open onToggle={() => setLeftOpen(false)} />
        <a href={toPath('work/specs')} className="plain-link small">‹ 문서·기획서 목록</a>
        <section className="sp-props" aria-label="필수 항목">
          <header className="sp-props-head">
            <h2>{TEMPLATE_NAME[tpl]} 항목</h2>
          </header>
          <div className="sp-fill">
            <button type="button" className="sp-fill-btn" disabled={!hasFree || filling} title={hasFree ? 'AI가 자유 작성을 읽고 필수 항목을 다시 써요 (저장 전까지 반영 안 됨, 취소로 되돌림)' : '가운데 자유 작성을 먼저 써 주세요'}
              onClick={() => void autoFill()}>{filling ? '읽는 중…' : '✦ 자동 채우기'}</button>
          </div>
          {(suggesting || suggCount > 0) && (
            <div className="sp-sugg-bar">
              <span>{suggesting ? '빈 칸 제안 만드는 중…' : `빈 칸 AI 제안 ${suggCount}`}</span>
              {!suggesting && suggCount > 1 && <button type="button" onClick={applyAll}>모두 적용</button>}
            </div>
          )}

          {spec.question && (
            <button type="button" className={`sp-question${answerFound ? ' answered' : ''}`} onClick={() => jump('sec-question')}>
              <span className="sp-q-label">{answerFound ? '질문 답 찾음 · 저장하면 보내요' : '답할 질문'}</span>
              <span className="sp-q-text">{spec.question}</span>
              {answerFound && <span className="sp-q-answer">{answerFound}</span>}
            </button>
          )}

          {sections.map(sectionEl)}
        </section>

        <nav className="sp-links" aria-label="이 기획서">
          <details className="sp-decisions" id="sec-decisions">
            <summary>결정 이력 {decisions.length}건</summary>
            {decisions.length ? (
              <ul>
                {decisions.map((x) => <li key={x.id}><span className="sp-pid">{x.id}</span><span>{x.text}<HrRecord d={x} /></span></li>)}
              </ul>
            ) : <p className="sp-empty">아직 없음</p>}
          </details>
          {thread && <a href={toPath(`talk/${thread.id}`)}>문서 스레드 ({thread.messages.length})</a>}
        </nav>
      </aside>}

      <section className="col-doc">
        <div className="doc-actions spec-actions">
          {editing ? (
            <>
              {dirty && <span className="spec-dirty">저장 안 됨</span>}
              <Button size="small" onClick={cancel}>취소</Button>
              <Button size="small" variant="primary" onClick={saveAll}>저장</Button>
            </>
          ) : (
            <>
              <Button size="small" variant="primary" onClick={startEdit}>수정</Button>
              <Button size="small" onClick={() => { setHistoryOpen(!historyOpen) }} aria-pressed={historyOpen}>수정 이력</Button>
              <Button size="small" onClick={removeSpec} disabled={!deletable} title={deletable ? '기획서 초안 지우기' : '확정됐거나 작업이 나간 기획서는 지울 수 없어요'}>삭제</Button>
            </>
          )}
        </div>
        {editing
          ? <input className="doc-title-input" value={cur.title} onChange={(e) => set({ title: e.target.value })} aria-label="기획서 제목" />
          : <h1 className="doc-title-static">{spec.title}</h1>}
        <div className="doc-head" style={{ marginTop: 4 }}>
          <State v={xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : spec.status} />
          {spec.confirmedBy && <Tag>{CONFIRMED_BY[spec.confirmedBy]}</Tag>}
          <span className="muted small">요청 <Ref s={s} id={spec.request} /> · <a href={toPath(`projects/${spec.request}`)}>프로젝트 보기</a></span>
        </div>
        {xr && <ReviewDecide d={d} spec={spec} xr={xr} />}
        <PlanHandoff s={s} d={d} spec={spec} />
        {spec.status === '확정' && <ReleaseCheckBox d={d} spec={spec.id} />}
        <FreeCanvas value={cur.bodyMd} editing={editing} onChange={(bodyMd) => set({ bodyMd })} />
        {spec.question && (
          <div className="question" id="sec-question">
            <b className="question-label">답할 질문</b>
            <p className="question-text">{spec.question}</p>
            <form className="spec-answer" onSubmit={(e) => { e.preventDefault(); if (!answer.trim()) return; d({ type: 'answerQuestion', spec: spec.id, text: answer.trim() }); setAnswer(''); toast.show(`${spec.id} 질문에 답함`) }}>
              <input className="field" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="답 적기" aria-label="질문에 답" />
              <Button size="small" variant="primary" type="submit" disabled={!answer.trim()}>답하기</Button>
            </form>
          </div>
        )}
        <h3 className="sec">근거</h3>
        <span className="tags">{spec.basis.map((b) => <Tag key={b}>{b}</Tag>)}</span>
      </section>

      {historyOpen && <History s={s} spec={spec} onClose={() => setHistoryOpen(false)} />}
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
