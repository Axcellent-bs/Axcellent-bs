// 기획서 화면: 문서가 원본이고 문단에 작업·PR·결정이 붙는다
import { type Dispatch } from 'react'
import { Button, Notice } from '@dejafix/common-frontend'
import { type Action, type PR, type Spec, type State as S } from '@/lib/planningData'
import { ActivityList, Sec, State, Tag, rowNav } from '@/components/planning-common/parts'
import { Linked, Ref, go, hrefOf, prSlug, reviewOf, toPath } from '@/components/planning-common/links'
import { CONFIRMED_BY, mergedLabel } from '@/components/planning-common/flow'
import { RepoTag } from '@/components/planning-common/repo'

interface P { s: S; d: Dispatch<Action> }

// ---------------------------------------------------------------- 기획 문서 (문서가 원본, 문단에 모든 것이 붙는다)
export function Specs({ s, d, item, item2 }: P & { item?: string; item2?: string }) {
  const spec = s.specs.find((x) => x.id === item) ?? s.specs.find((x) => x.id === 'SPEC-7') ?? s.specs[0]
  if (!spec) return <div className="empty"><b>기획서 없음</b></div>
  return <SpecView s={s} d={d} spec={spec} item2={item2} />
}

function SpecView({ s, d, spec, item2 }: P & { spec: Spec; item2?: string }) {
  const para = spec.paras.find((p) => p.id === item2) ?? spec.paras.find((p) => p.id === (spec.id === 'SPEC-7' ? 'R2' : '')) ?? spec.paras[0]
  const paraHref = (id: string) => toPath(`work/specs/${spec.id}/${id}`)
  const setParaId = (id: string) => go(paraHref(id))
  const tasks = s.tasks.filter((t) => t.spec === spec.id && t.para === para.id)
  const prs = s.prs.filter((p) => tasks.some((t) => t.id === p.task))
  const decisions = s.decisions.filter((x) => x.spec === spec.id)
  const thread = s.threads.find((t) => t.on === spec.id)
  const inc = s.incidents.find((i) => i.candidates.some((c) => c.spec === spec.id && c.para === para.id))
  const xr = reviewOf(s, spec.id)

  return (
    <div className="three">
      <aside className="col-left">
        <div className="ph">기획 문서</div>
        {s.specs.map((x) => (
          <a key={x.id} href={toPath(`work/specs/${x.id}`)} className={`doc-li${x.id === spec.id ? ' on' : ''}`}>
            <span className="mono">{x.id}</span> {x.title} <State v={x.status} />
          </a>
        ))}
        <div className="ph" style={{ marginTop: 18 }}>{spec.id} 개요</div>
        <ul className="outline">
          <li>문제 · 목표</li>
          {spec.paras.map((p) => (
            <li key={p.id}><a href={paraHref(p.id)} className={p.id === para.id ? 'on' : ''}>{p.id} {p.kind === 'req' ? '요구사항' : '완료 기준'}</a></li>
          ))}
          {spec.question && <li>{thread ? <a href={toPath(`talk/${thread.id}`)}>질문 1개 남음</a> : '질문 1개 남음'}</li>}
          <li>결정 이력 {decisions.length}건</li>
          {thread && <li><a href={toPath(`talk/${thread.id}`)}>문서 스레드 열기</a></li>}
        </ul>
      </aside>

      <section className="col-doc">
        <div className="doc-head">
          <h1 className="page-title" style={{ margin: 0 }}>{spec.title}</h1>
          <State v={xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : spec.status} />
          {spec.confirmedBy && <Tag>{CONFIRMED_BY[spec.confirmedBy]}</Tag>}
          <span style={{ flex: 1 }} />
        </div>
        <p className="muted small">
          요청 <Ref s={s} id={spec.request} /> · <a href={toPath(`projects/${spec.request}`)}>프로젝트 보기</a> · 에이전트가 작성, 사람이 쓴 문장 {spec.humanSentences}개
        </p>
        {spec.status === '초안' && !xr && spec.question && <Notice tone="warn">빈 항목 질문이 남아 있습니다. 답이 오면 AI가 확정 조건(질문 없음·인사 경고 없음·근거 있음)을 다시 보고, 맞으면 바로 확정하고 아니면 AI 교차검증에 올립니다.</Notice>}
        {xr && (
          <Notice tone="warn">
            {xr.lane === 'human'
              ? <>되돌릴 수 없는 일({xr.irreversible})이라 AI가 정하지 않습니다. <a href={toPath(`work/reviews/${xr.id}`)}>사람 확인 대기열 {xr.id}</a>에서 팀장이 확인합니다.</>
              : <>자동 확정 조건에 맞지 않아({xr.routeWhy.join(', ')}) <a href={toPath(`work/reviews/${xr.id}`)}>AI 교차검증 {xr.id}</a>에 올라가 있습니다. 판단은 {xr.verifier}가 합니다.</>}
          </Notice>
        )}
        <h3 className="sec">문제</h3><p>{spec.problem}</p>
        <h3 className="sec">목표</h3><p>{spec.goal}</p>
        <h3 className="sec">요구사항</h3>
        {spec.paras.filter((p) => p.kind === 'req').map((p) => <ParaBox key={p.id} p={p} on={p.id === para.id} onClick={() => setParaId(p.id)} s={s} spec={spec.id} />)}
        <h3 className="sec">완료 기준</h3>
        {spec.paras.filter((p) => p.kind === 'done').map((p) => <ParaBox key={p.id} p={p} on={p.id === para.id} onClick={() => setParaId(p.id)} s={s} spec={spec.id} />)}
        {spec.question && (
          <div className="question">
            질문 · {spec.question}
          </div>
        )}
        <h3 className="sec">근거</h3>
        <span className="tags">{spec.basis.map((b) => <Tag key={b}>{b}</Tag>)}</span>
      </section>

      <aside className="col-right">
        <div className="ph">선택한 문단 {para.id}에 연결된 것</div>
        <Sec title="작업" sub={`${tasks.length}건`}>
          {tasks.length ? tasks.map((t) => <div key={t.id} {...rowNav(hrefOf(s, t.id), '이 작업의 PR 보기')}><div className="mini-row"><span className="mono">{t.id}</span> {t.title} <State v={t.state} /></div></div>) : <span className="muted">기획서가 확정되면 split_tasks가 나눈다</span>}
        </Sec>
        <Sec title="PR" sub="작업 1개 = PR 1개">
          {prs.length ? prs.map((p) => <PrLine key={p.id} s={s} p={p} d={d} />) : <span className="muted">아직 없음</span>}
        </Sec>
        <Sec title="스레드 · 결정" sub={thread ? <a href={toPath(`talk/${thread.id}`)}>스레드 전체 보기</a> : undefined}>
          {thread?.messages.slice(-2).map((m, i) => <div key={i} className="msg"><b>{m.who}</b> <Linked s={s} text={m.text} />{m.decision && <> <Tag tone="green">{m.decision}</Tag></>}</div>)}
          {decisions.map((x) => <div key={x.id} className="msg"><span className="mono">{x.id}</span> {x.text} {x.sentToHr && <span className="muted">· 인사에 ① 전송</span>}</div>)}
        </Sec>
        <Sec title="장애 연결" sub="DejaFix 사후관리">
          {inc ? <div className="msg"><Ref s={s} id={inc.ctx} /> {inc.title} → 관련 PR {inc.candidates.map((c, i) => <span key={c.pr}>{i > 0 && ', '}<Ref s={s} id={c.pr} /></span>)}</div> : <span className="muted">연결된 사건 없음</span>}
        </Sec>
        <div className="ph" style={{ marginTop: 6 }}>최근 에이전트 활동</div>
        <ActivityList s={s} items={s.activity.filter((a) => a.ctx === (s.requests.find((r) => r.id === spec.request)?.ctx ?? 'ctx_req_0012')).slice(-4).reverse()} compact />
      </aside>
    </div>
  )
}

function ParaBox({ p, on, onClick, s, spec }: { p: S['specs'][number]['paras'][number]; on: boolean; onClick: () => void; s: S; spec: string }) {
  const tasks = s.tasks.filter((t) => t.spec === spec && t.para === p.id)
  const prs = s.prs.filter((x) => tasks.some((t) => t.id === x.task))
  const done = tasks.length > 0 && tasks.every((t) => t.state === '완료')
  return (
    <button type="button" className={`para${on ? ' on' : ''}`} onClick={onClick}>
      <div><span className="pid">{p.id}</span>{p.text}</div>
      {p.warn && <div className="warnline">인사운영 ② 경고 ({p.warn.card}) · {p.warn.text}</div>}
      {p.incident && <div className="incline">장애 표시 · {p.incident} (link_incident)</div>}
      {(tasks.length > 0 || done) && (
        <span className="tags">
          {tasks.map((t) => <Tag key={t.id}>{t.id}</Tag>)}
          {prs.map((x) => <Tag key={x.id}>{x.id}</Tag>)}
          {done && <Tag tone="green">구현됨</Tag>}
        </span>
      )}
    </button>
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
