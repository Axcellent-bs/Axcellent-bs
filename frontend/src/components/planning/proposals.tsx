// 기획 제안: ① 데이터분석 인사이트(근거 수치) → ② 기획 판단(할 일·이유·목표) → 사람 확정(기획안으로 만들기)·폐기
// 마케팅·개발로 넘기는 건 기획안 다음 단계
import { useState, type Dispatch } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, type Action, type State as S } from '@/lib/planningData'
import { PROPOSAL_SKILL_LABEL, PROPOSAL_STAGES, WRITTEN_BY_LABEL, proposalEnded, proposalOpen, proposalStage, type Proposal, type ProposalFact } from '@/lib/proposals'
import { Tag } from '@/components/planning-common/parts'
import { go, toPath } from '@/components/planning-common/links'
import { Pager, usePage } from '@/components/planning-common/pager'
import { Markdown } from '@/components/planning-common/markdown'

interface P { s: S; d: Dispatch<Action> }

const STATUS_TONE: Record<Proposal['status'], 'amber' | 'neutral' | 'green'> = {
  '확인 대기': 'amber', '기획안': 'green', '마케팅 전달': 'neutral', '진행 중': 'neutral', '성과 확인': 'green', '기획서로': 'neutral', '폐기': 'neutral',
}

/** 제안 상세의 주 버튼 (머리 줄 데이터 보기 토글 자리): 기획안으로 만들기 · 폐기. 확인 대기일 때만 */
export function ProposalActions({ s, d, item }: P & { item?: string }) {
  const toast = useToast()
  const [reason, setReason] = useState<string | null>(null)
  const p = item ? (s.proposals ?? []).find((x) => x.id === item) : undefined
  if (!p) return null
  const remove = () => {
    const sent = !!p.handoff?.ok
    if (!window.confirm(`${p.id} "${p.title}" 제안을 지울까요?${p.spec ? ` 기획안 ${p.spec}와 요청도 같이 지워져요.` : ''}${sent ? ' 마케팅에 넘긴 기획안은 마케팅 쪽에 남아요.' : ''}`)) return
    d({ type: 'deleteProposal', proposal: p.id }); toast.show(`${p.id} 삭제`); go(toPath('proposals'))
  }
  const reqSpec = s.requests.find((r) => r.id === p.request)?.spec
  const deletable = !s.tasks.some((t) => t.spec === p.spec || (!!reqSpec && t.spec === reqSpec))
  if (!proposalOpen(p)) return deletable ? <Button onClick={remove}>삭제</Button> : null
  const nextSpec = `SPEC-${Math.max(0, ...s.specs.map((x) => Number(x.id.split('-')[1]) || 0)) + 1}`
  if (reason !== null) {
    return (
      <form className="pp-head-reject" onSubmit={(e) => { e.preventDefault(); if (!reason.trim()) return; d({ type: 'rejectProposal', proposal: p.id, reason: reason.trim() }); setReason(null); toast.show(`${p.id} 폐기`) }}>
        <input className="input" autoFocus placeholder="폐기 이유" aria-label="폐기 이유" value={reason} onChange={(e) => setReason(e.target.value)} />
        <Button variant="primary" type="submit" disabled={!reason.trim()}>폐기</Button>
        <Button onClick={() => setReason(null)}>취소</Button>
      </form>
    )
  }
  return (
    <>
      <Button variant="primary" onClick={() => { d({ type: 'makePlan', proposal: p.id }); toast.show(`${p.id} 기획안으로 만듦`); go(toPath(`work/specs/${nextSpec}`)) }}>기획안으로 만들기</Button>
      <Button onClick={() => setReason('')}>폐기</Button>
    </>
  )
}

export function Proposals({ s, d, item }: P & { item?: string }) {
  const xs = s.proposals ?? []
  const p = item ? xs.find((x) => x.id === item) : undefined
  if (item && p) return <ProposalView s={s} d={d} p={p} />
  return <ProposalList s={s} d={d} />
}

function ProposalList({ d, s }: P) {
  const toast = useToast()
  const xs = (s.proposals ?? []).slice().reverse()
  const [stage, setStage] = useState<number | null>(null)
  const count = (n: number) => xs.filter((p) => proposalStage(p) === n && !proposalEnded(p)).length
  const rows = stage === null ? xs : xs.filter((p) => proposalStage(p) === stage)
  const pg = usePage(rows, 12)
  return (
    <div className="sl-wrap">
      <div className="sl-top">
        <div className="seg-toggle" role="group" aria-label="단계">
          <button type="button" aria-pressed={stage === null} onClick={() => { setStage(null); pg.setPage(0) }}>전체 {xs.length}</button>
          {PROPOSAL_STAGES.map((label, n) => (
            <button key={label} type="button" aria-pressed={stage === n} onClick={() => { setStage(n); pg.setPage(0) }}>{label} {count(n)}</button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <Button size="small" variant="primary" onClick={() => { d({ type: 'pullInsights' }); toast.show('데이터분석에서 인사이트를 받아오는 중') }}>데이터분석에서 받아오기</Button>
      </div>
      {!pg.rows.length ? <div className="empty"><b>없음</b></div> : (
        <>
          <div className="inc-grid rq-cards">
            {pg.rows.map((p) => <ProposalCard key={p.id} p={p} />)}
          </div>
          <Pager {...pg} />
        </>
      )}
    </div>
  )
}

function Steps({ p }: { p: Proposal }) {
  const at = proposalStage(p)
  return (
    <ol className="inc-steps" aria-label="진행 단계">
      {PROPOSAL_STAGES.map((label, i) => (
        <li key={label} className={`${i <= at ? 'on' : ''}${i === at ? ' now' : ''}${i === at && proposalOpen(p) ? ' warn' : ''}`}>{label}</li>
      ))}
    </ol>
  )
}

function ProposalCard({ p }: { p: Proposal }) {
  const wait = proposalOpen(p)
  return (
    <a href={toPath(`proposals/${p.id}`)} className={`inc-card rq-card${wait ? ' wait' : ''}${proposalEnded(p) ? ' fin' : ''}`}>
      <span className="inc-card-top">
        <span className={`rq-dept d-${p.from}`}>{DEPT_NAME[p.from]}</span>
        <Tag>{PROPOSAL_SKILL_LABEL[p.skill] ?? p.skill}</Tag>
        <span className="mono">{p.id}</span>
      </span>
      <b className="inc-card-title">{p.title}</b>
      <span className="pp-idea">{p.idea}</span>
      <Steps p={p} />
      <span className="inc-card-meta pp-meta">
        <Tag tone={STATUS_TONE[p.status]}>{p.status}</Tag>
        {p.target && <span className="pp-target-chip">목표 {p.target.value}</span>}
        {p.warnings.length > 0 && <Tag tone="amber">숫자 확인 {p.warnings.length}</Tag>}
        <span className="muted small">근거 {p.facts.length}{p.result ? ` · ${p.result.direction}` : ''}</span>
      </span>
    </a>
  )
}

function Facts({ facts }: { facts: ProposalFact[] }) {
  return (
    <table className="pp-facts">
      <tbody>
        {facts.map((f, i) => (
          <tr key={i}><td>{f.label}</td><td className="pp-num">{f.value}</td><td className="mono muted small">{f.link ? <a href={f.link} target="_blank" rel="noreferrer">{f.source} ↗</a> : f.source}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

// 성과 근거 구분 (백엔드 proposals.py EFFECT_*). 같은 주문이 겹칠 수 있어 더하지 않고 나눠 보여 준다
const EFFECT_GROUPS: { source: string; label: string }[] = [
  { source: 'campaign_effect · 상품 기준', label: '상품 기준' },
  { source: 'campaign_effect · 유입 기준', label: '유입 기준 · 캠페인 링크' },
  { source: 'campaign_effect · 쿠폰 귀속', label: '쿠폰 귀속' },
  { source: 'campaign_effect · 가게 전체', label: '가게 전체 · 참고' },
]

function EffectFacts({ facts }: { facts: ProposalFact[] }) {
  const known = new Set(EFFECT_GROUPS.map((g) => g.source))
  const rest = facts.filter((f) => !known.has(f.source))
  return (
    <div className="pp-effect">
      {EFFECT_GROUPS.map((g) => {
        const rows = facts.filter((f) => f.source === g.source)
        if (!rows.length) return null
        return (
          <div key={g.source} className="pp-effect-group">
            <div className="pp-effect-head">{g.label}</div>
            {rows.map((f) => <div key={f.label} className="small">{f.label} <b>{f.value}</b></div>)}
          </div>
        )
      })}
      {rest.map((f) => <div key={f.label} className="small">{f.label} <b>{f.value}</b></div>)}
    </div>
  )
}

function ProposalView({ s, d, p }: P & { p: Proposal }) {
  const toast = useToast()
  const open = proposalOpen(p)
  const save = (k: 'title' | 'idea' | 'why', v: string) => { if (v.trim() && v.trim() !== p[k]) d({ type: 'editProposal', proposal: p.id, [k]: v.trim() }) }
  const [tLabel, setTLabel] = useState(p.target?.label ?? '')
  const [tValue, setTValue] = useState(p.target?.value ?? '')
  const saveTarget = () => { if (tLabel.trim() && tValue.trim() && (tLabel.trim() !== p.target?.label || tValue.trim() !== p.target?.value)) d({ type: 'editProposal', proposal: p.id, targetLabel: tLabel.trim(), targetValue: tValue.trim() }) }
  const req = p.request ? s.requests.find((r) => r.id === p.request) : undefined
  return (
    <div className={`pp-view${open ? " solo" : ""}`}>
      <div className="pp-main">
        <a className="plain-link proj-back" href={toPath('proposals')} onClick={(e) => { e.preventDefault(); go(toPath('proposals')) }}>‹ 제안 목록</a>
        <div className="pp-head">
          <span className={`rq-dept d-${p.from}`}>{DEPT_NAME[p.from]}</span>
          <Tag>{PROPOSAL_SKILL_LABEL[p.skill] ?? p.skill}</Tag>
          <span className="mono muted">{p.id}{p.insightId ? ` · ${p.insightId}` : ''}</span>
          <span style={{ flex: 1 }} />
          <Tag tone={STATUS_TONE[p.status]}>{p.status}</Tag>
          <span className="muted small">{p.created}</span>
        </div>
        {open
          ? <input key={`t-${p.id}`} className="doc-title-input" defaultValue={p.title} onBlur={(e) => save('title', e.target.value)} aria-label="제목" />
          : <h2 className="pp-title">{p.title}</h2>}
        <Steps p={p} />

        <section className="pp-part">
          <h3 className="pp-part-h"><span className="pp-no">1</span>{DEPT_NAME[p.from]} 인사이트</h3>
          <p className="pp-insight">{p.insight}</p>
          <Facts facts={p.facts} />
          {(p.audience || p.shortsFormat || p.products.length > 0) && (
            <dl className="pp-meta-dl">
              {p.audience && <><dt>타깃</dt><dd>{p.audience}</dd></>}
              {p.shortsFormat && <><dt>숏츠 형식</dt><dd>{p.shortsFormat}</dd></>}
              {p.products.length > 0 && <><dt>상품</dt><dd className="pp-chips">{p.products.map((x) => <Tag key={x}>{x}</Tag>)}</dd></>}
            </dl>
          )}
          {p.bodyMd && (
            <details className="pp-body">
              <summary>설명</summary>
              <Markdown text={p.bodyMd} className="pp-md" />
            </details>
          )}
        </section>

        <section className="pp-part">
          <h3 className="pp-part-h"><span className="pp-no">2</span>기획 판단<span className="pp-by">{WRITTEN_BY_LABEL[p.writtenBy]}</span></h3>
          <dl className="pp-fields">
            <dt>할 일</dt>
            <dd>{open ? <textarea key={`i-${p.id}`} className="blk-p" rows={2} defaultValue={p.idea} onBlur={(e) => save('idea', e.target.value)} aria-label="할 일" /> : p.idea}</dd>
            <dt>왜</dt>
            <dd>{open ? <textarea key={`w-${p.id}`} className="blk-p" rows={2} defaultValue={p.why} onBlur={(e) => save('why', e.target.value)} aria-label="왜" /> : p.why}</dd>
            <dt>목표</dt>
            <dd>
              {open ? (
                <span className="pp-target-edit">
                  <input className="blk-p" value={tLabel} placeholder="무엇을" onChange={(e) => setTLabel(e.target.value)} onBlur={saveTarget} aria-label="목표 항목" />
                  <input className="blk-p pp-target-val" value={tValue} placeholder="얼마나" onChange={(e) => setTValue(e.target.value)} onBlur={saveTarget} aria-label="목표치" />
                </span>
              ) : p.target ? <><b>{p.target.value}</b> {p.target.label}</> : <span className="muted">없음</span>}
              {p.target?.measure && <span className="mono muted small"> · {p.target.measure}</span>}
            </dd>
            {p.format && <><dt>형식</dt><dd><Tag>{p.format}</Tag></dd></>}
          </dl>
          {p.warnings.length > 0 && <ul className="pp-warn">{p.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        </section>
      </div>

      {!open && (
        <aside className="pp-side">
          <dl className="pp-out">
            {p.spec && <><dt>기획안</dt><dd><a href={toPath(`work/specs/${p.spec}`)}>{p.spec}</a> <span className="muted small">{s.specs.find((x) => x.id === p.spec)?.status ?? ''}</span></dd></>}
            {p.handoff && <><dt>마케팅</dt><dd>{p.handoff.ok ? <Tag>받음{p.handoff.campaign ? ` · ${p.handoff.campaign}` : ''}</Tag> : <Tag tone="amber" title={p.handoff.reason}>전달 실패</Tag>}{p.handoff.at && <span className="muted small"> {p.handoff.at}</span>}{!p.handoff.ok && p.handoff.reason && <div className="muted small">{p.handoff.reason}</div>}</dd></>}
            {p.request && p.status === '기획서로' && <><dt>개발</dt><dd><a href={toPath(`projects/${p.request}`)}>{p.request}</a>{req && <span className="muted small"> {req.state}</span>}</dd></>}
            {p.result && (
              <>
                <dt>성과</dt>
                <dd>
                  <Tag tone={p.result.direction === '늘었음' ? 'green' : p.result.direction === '줄었음' ? 'amber' : 'neutral'}>{p.result.direction}</Tag> {p.result.summary}
                  {p.target && <div className="small">목표 {p.target.value}</div>}
                  <EffectFacts facts={p.result.facts ?? []} />
                  <div className="muted small">{p.result.at} · 데이터분석</div>
                </dd>
              </>
            )}
            {p.note && <><dt>{p.status === '폐기' ? '폐기 이유' : '메모'}</dt><dd>{p.note}</dd></>}
            {p.handoff?.campaign && (
              <><dt /><dd><Button size="small" onClick={() => { d({ type: 'measureProposal', proposal: p.id }); toast.show(`${p.id} 성과 재는 중`) }}>{p.result ? '성과 다시 재기' : '성과 재기'}</Button></dd></>
            )}
          </dl>
        </aside>
      )}
    </div>
  )
}
