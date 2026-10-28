// 기획개발팀 탭의 하위 화면. 작업·운영·사후관리·교차검증은 프로젝트 탭과 같은 틀(왼쪽 목록 / 가운데 세로 흐름 / 오른쪽 곁 블록)을 쓴다
import { useEffect, useState, type Dispatch, type ReactNode } from 'react'
import { Button, Card, Notice, useToast, type Column } from '@dejafix/common-frontend'
import { DEPT_NAME, IRREVERSIBLE, PORTS, TARGET_LABEL, TODAY, GUARD, OBSERVE_MIN, OBSERVE_N, activeRules, ruleText, type Action, type Activity as Act, type AuditItem, type AutoRule, type CrossReview, type Dept, type PR, type Spec, type State as S } from './data'
import { codeOf, traceOf, type CodeLine } from './trace'
import { ActivityList, Dep, Grade, Lead, LinkTable, Point, Sec, State, Stats, rowNav, Tag } from './parts'
import { Linked, Ref, convoHref, go, hrefOf, prFromSlug, prSlug, reviewOf } from './links'
import { CONFIRMED_BY, Flow, Head, Layout, ListPane, Pairs, Side, Step, mergedLabel } from './flow'
import { SendWindow } from './depts'

interface P { s: S; d: Dispatch<Action> }

// 요청 탭은 requests.tsx (모든 요청의 단계 한눈에)

// ---------------------------------------------------------------- 기획 문서 (문서가 원본, 문단에 모든 것이 붙는다)
export function Specs({ s, d, item, item2 }: P & { item?: string; item2?: string }) {
  const spec = s.specs.find((x) => x.id === item) ?? s.specs.find((x) => x.id === 'SPEC-7') ?? s.specs[0]
  if (!spec) return <div className="empty"><b>기획서 없음</b></div>
  return <SpecView s={s} d={d} spec={spec} item2={item2} />
}

function SpecView({ s, d, spec, item2 }: P & { spec: Spec; item2?: string }) {
  const para = spec.paras.find((p) => p.id === item2) ?? spec.paras.find((p) => p.id === (spec.id === 'SPEC-7' ? 'R2' : '')) ?? spec.paras[0]
  const paraHref = (id: string) => `#/work/specs/${spec.id}/${id}`
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
          <a key={x.id} href={`#/work/specs/${x.id}`} className={`doc-li${x.id === spec.id ? ' on' : ''}`}>
            <span className="mono">{x.id}</span> {x.title} <State v={x.status} />
          </a>
        ))}
        <div className="ph" style={{ marginTop: 18 }}>{spec.id} 개요</div>
        <ul className="outline">
          <li>문제 · 목표</li>
          {spec.paras.map((p) => (
            <li key={p.id}><a href={paraHref(p.id)} className={p.id === para.id ? 'on' : ''}>{p.id} {p.kind === 'req' ? '요구사항' : '완료 기준'}</a></li>
          ))}
          {spec.question && <li>{thread ? <a href={`#/talk/${thread.id}`}>질문 1개 남음</a> : '질문 1개 남음'}</li>}
          <li>결정 이력 {decisions.length}건</li>
          {thread && <li><a href={`#/talk/${thread.id}`}>문서 스레드 열기</a></li>}
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
          요청 <Ref s={s} id={spec.request} /> · <a href={`#/projects/${spec.request}`}>프로젝트 보기</a> · 에이전트가 작성, 사람이 쓴 문장 {spec.humanSentences}개
        </p>
        {spec.status === '초안' && !xr && spec.question && <Notice tone="warn">빈 항목 질문이 남아 있습니다. 답이 오면 AI가 확정 조건(질문 없음·인사 경고 없음·근거 있음)을 다시 보고, 맞으면 바로 확정하고 아니면 AI 교차검증에 올립니다.</Notice>}
        {xr && (
          <Notice tone="warn">
            {xr.lane === 'human'
              ? <>되돌릴 수 없는 일({xr.irreversible})이라 AI가 정하지 않습니다. <a href={`#/work/reviews/${xr.id}`}>사람 확인 대기열 {xr.id}</a>에서 팀장이 확인합니다.</>
              : <>자동 확정 조건에 맞지 않아({xr.routeWhy.join(', ')}) <a href={`#/work/reviews/${xr.id}`}>AI 교차검증 {xr.id}</a>에 올라가 있습니다. 판단은 {xr.verifier}가 합니다.</>}
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
        <Sec title="스레드 · 결정" sub={thread ? <a href={`#/talk/${thread.id}`}>스레드 전체 보기</a> : undefined}>
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
      <div><a href={`#/work/prs/${prSlug(p.id)}`}><b>{p.id}</b> {p.title}</a></div>
      <div className="muted small mono">{p.gate.lines}줄·{p.gate.files}파일 · {p.gate.paths}</div>
      <span className="tags">
        <Tag tone={last === '통과' ? 'green' : 'amber'}>검수 {last}{p.reviews.length > 1 ? ` (${p.reviews.length}회차)` : ''}</Tag>
        {p.merged ? <Tag tone="green">{mergedLabel(p)}</Tag> : xr ? <a href={`#/work/reviews/${xr.id}`}><Tag tone="amber">{xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기'} {xr.id}</Tag></a> : rejected ? <Tag tone="amber">검증자 거절</Tag> : null}
      </span>
      {!p.merged && (last === '불일치' || rejected) && <Button size="small" onClick={() => d({ type: 'reviewPr', pr: p.id })}>다시 검수</Button>}
    </div>
  )
}

// ---------------------------------------------------------------- 작업·PR (왼쪽 작업 목록 / 가운데 PR 흐름 / 오른쪽 근거 체인)
const waitLabel = (x: CrossReview) => (x.lane === 'human' ? '사람 확인 대기' : '교차검증 대기')
export function Work({ s, d, item }: P & { item?: string }) {
  const [line, setLine] = useState<CodeLine>()
  useEffect(() => setLine(undefined), [item])
  const prOf = (taskId: string) => s.prs.find((p) => p.task === taskId)
  const sel = s.prs.find((p) => p.id === prFromSlug(item)) ?? s.prs.find((p) => reviewOf(s, p.id)) ?? s.prs[s.prs.length - 1]
  const groupOf = (taskId: string, state: string) => { const p = prOf(taskId); return state === '완료' || p?.merged ? 2 : p && reviewOf(s, p.id) ? 1 : 0 }
  const tasks = s.tasks.slice().reverse()
  const row = (t: S['tasks'][number]) => {
    const p = prOf(t.id)
    const xr = p && reviewOf(s, p.id)
    return { key: t.id, href: p ? `#/work/prs/${prSlug(p.id)}` : `#/work/specs/${t.spec}/${t.para}`, on: !!p && p.id === sel?.id, title: t.title, meta: <>{t.id}{p ? ` · ${p.id}` : ''} <State v={xr ? waitLabel(xr) : t.state} /></> }
  }
  const list = <ListPane empty="작업 없음" groups={(['진행 중', '교차검증·사람 확인', '완료'] as const).map((label, i) => ({ label, rows: tasks.filter((t) => groupOf(t.id, t.state) === i).map(row) }))} />
  if (!sel) return <Layout list={list}><div className="empty"><b>PR 없음</b></div></Layout>
  const task = s.tasks.find((t) => t.id === sel.task)
  const spec = s.specs.find((x) => x.id === task?.spec)
  const para = spec?.paras.find((p) => p.id === task?.para)
  const xr = reviewOf(s, sel.id)
  const last = sel.reviews[sel.reviews.length - 1]
  const rejected = !sel.merged && !xr && s.reviews.some((x) => x.ref === sel.id && x.result === '거절')
  const chain = traceOf(s, sel.id).filter((x) => x.kind !== '코드' && x.kind !== 'PR')
  return (
    <Layout
      list={list}
      head={<Head kicker={<>{sel.id} · {task?.id} · <span className="mono">{sel.branch}</span></>} title={sel.title.replace(/^[A-Z]\d+: /, '')} side={<State v={sel.merged ? '완료' : xr ? waitLabel(xr) : last === '불일치' || rejected ? '불일치' : '머지 대기'} />} />}
      side={
        <Side title="근거 체인" sum={`${chain.length}단계`}>
          {chain.map((x, i) => <p key={i} className="pside-msg"><b>{x.kind}</b> {x.href ? <a href={x.kind === '문단' && spec && para ? `#/work/specs/${spec.id}/${para.id}` : x.href} className="plain-link">{x.title}</a> : x.title}</p>)}
        </Side>
      }
      below={<Trace s={s} pr={sel} line={line} setLine={setLine} />}
    >
      <Flow>
        <Step name="작업" on href={task ? `#/work/specs/${task.spec}/${task.para}` : undefined} title={task ? `${task.id} · ${task.spec} ${task.para}` : '-'}>
          {para && <p>{para.text}</p>}
          {para?.warn && <Tag tone="amber">경고 {para.warn.card}</Tag>}
          {para?.incident && <Tag tone="amber">장애 {para.incident}</Tag>}
        </Step>
        <Step name="관문" on title={`${sel.gate.result} · ${sel.gate.lines}줄·${sel.gate.files}파일 · ${sel.gate.paths}`} />
        <Step name="검수" on={last === '통과'} title={sel.reviews.join(' → ')}>
          {!sel.merged && (last === '불일치' || rejected) && <Button size="small" onClick={() => d({ type: 'reviewPr', pr: sel.id })}>다시 검수</Button>}
        </Step>
        <Step name="머지" on={sel.merged} href={xr ? `#/work/reviews/${xr.id}` : undefined} title={sel.merged ? mergedLabel(sel) : xr ? `${waitLabel(xr)} · ${xr.id}` : rejected ? '검증자 거절' : '머지 전'}>
          {xr && <p className="muted">{xr.verifier}</p>}
        </Step>
      </Flow>
    </Layout>
  )
}

// 코드 역추적 (코드 줄 → PR → 작업 → 문단 → 결정 → 요청 → 근거). 연결은 문단 id로만 따라간다
function Trace({ s, pr, line: picked, setLine }: { s: S; pr: PR; line?: CodeLine; setLine: (x?: CodeLine) => void }) {
  const lines = codeOf(s, pr)
  const line = picked ?? lines[0]
  const steps = traceOf(s, pr.id, line)
  return (
    <section id="trace" className="flow-sec">
      <h2>코드 역추적</h2>
      <div className="trace">
        <div className="trace-code">
          <div className="trace-h">바뀐 줄</div>
          {lines.map((l) => (
            <button key={`${l.path}:${l.line}`} type="button" className={`code-line${l.line === line.line && l.path === line.path ? ' on' : ''}`} onClick={() => setLine(l)}>
              <span className="muted small mono">{l.path}:{l.line}</span>
              <code>{l.text}</code>
            </button>
          ))}
        </div>
        <ol className="trace-chain">
          {steps.map((x, i) => (
            <li key={i} className="trace-step">
              <span className="trace-kind">{x.kind}</span>
              <div>
                <div>{x.href ? <a href={x.href}>{x.title}</a> : <b>{x.title}</b>}</div>
                {x.detail && <div className="muted small">{x.detail}</div>}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- AI 교차검증 대기열 + 사람 확인 (되돌릴 수 없는 일)
export function Reviews({ s, d, item }: P & { item?: string }) {
  const toast = useToast()
  const all = s.reviews.slice().reverse()
  const r = s.reviews.find((x) => x.id === item) ?? all.find((x) => !x.result) ?? all[0]
  const row = (x: CrossReview) => ({ key: x.id, href: `#/work/reviews/${x.id}`, on: x.id === r?.id, title: `${TARGET_LABEL[x.target]} · ${x.title}`, meta: <>{x.id} · {x.ref} <State v={x.result ?? waitLabel(x)} /></> })
  const list = <ListPane empty="대기 없음" groups={[
    { label: '교차검증 대기', rows: all.filter((x) => !x.result && x.lane === 'ai').map(row) },
    { label: '사람 확인 대기', rows: all.filter((x) => !x.result && x.lane === 'human').map(row) },
    { label: '끝남', rows: all.filter((x) => x.result).map(row) },
  ]} />
  if (!r) return <Layout list={list}><div className="empty"><b>대기 없음</b></div></Layout>
  const cnt = (k: CrossReview['checks'][number]['result']) => r.checks.filter((c) => c.result === k).length
  return (
    <Layout
      list={list}
      head={<Head kicker={<>{r.id} · {TARGET_LABEL[r.target]} · <Ref s={s} id={r.ref} /> · {r.t}</>} title={r.title} side={<State v={r.result ?? waitLabel(r)} />} />}
      side={
        <>
          <Side title="판단">
            <Pairs items={[
              ['검증자', r.verifier],
              ['자기 승인', r.lane === 'ai' ? '아님 · 기획개발 밖' : '해당 없음'],
              ...(r.irreversible ? [['되돌릴 수 없음', r.irreversible] as [string, string]] : []),
            ]} />
            {!r.result && r.lane === 'human' && (
              <div className="actions">
                <Button variant="primary" onClick={() => { d({ type: 'humanDecide', review: r.id, ok: true }); toast.show(`${r.ref} 진행`) }}>확인하고 진행</Button>
                <Button onClick={() => { d({ type: 'humanDecide', review: r.id, ok: false }); toast.show(`${r.ref} 보류`) }}>보류</Button>
              </div>
            )}
          </Side>
          <Side title="대화" href={convoHref(r.ctx)} sum={<span className="mono">{r.ctx}</span>} />
        </>
      }
    >
      <Flow>
        <Step name="이유" on title={r.routeWhy.join(', ') || '-'} />
        <Step name="AI 정리" on title={r.recommend ? `추천 ${r.recommend}` : '추천 없음'}>
          <p><Linked s={s} text={r.summary} /></p>
          {r.recommendWhy && <p className="muted">{r.recommendWhy}</p>}
        </Step>
        <Step name="규칙" on title={`통과 ${cnt('통과')} · 확인 필요 ${cnt('확인 필요')} · 걸림 ${cnt('걸림')}`}>
          <ul>{r.checks.map((c) => <li key={c.label}><State v={c.result} /> <b>{c.label}</b> <span className="muted"><Linked s={s} text={c.detail} /></span></li>)}</ul>
        </Step>
        <Step name="판단" on={!!r.result} title={r.result ? `${r.result} · ${r.decidedAt}` : '대기'}>
          {r.resultWhy && <p className="muted">{r.resultWhy}</p>}
        </Step>
        {r.target === 'merge_pr' && r.lane === 'ai' && (() => {
          const o = s.autoPolicy.observing.find((x) => x.pr === r.ref)
          const rule = s.autoPolicy.rules.find((x) => x.basis.includes(r.ref))
          return (
            <Step name="관찰" on={o?.result === '정상'} href={rule ? hrefOf(s, rule.id) : undefined} title={!o ? '-' : rule ? `${rule.id} ${rule.status}` : o.result}>
              {o && (
                <Pairs items={[
                  ['머지', o.mergedAt],
                  ['관찰', `${OBSERVE_MIN}분 · ${o.result}`],
                  ...(rule ? [['기준', <Ref key="r" s={s} id={rule.id} />] as [string, ReactNode]] : []),
                ]} />
              )}
            </Step>
          )
        })()}
      </Flow>
    </Layout>
  )
}

// ---------------------------------------------------------------- 자동 처리 범위 (학습된 기준: 머지 → 관찰 → 제안·백테스트 → 재무 AI·사람 승인 → 정식)
export function Autonomy({ s, d, item }: P & { item?: string }) {
  const toast = useToast()
  const pol = s.autoPolicy
  const by = (st: AutoRule['status']) => pol.rules.filter((r) => r.status === st).length
  const linked = pol.history.filter((h) => h.result === '장애 연결').length
  const off = (r: AutoRule) => { d({ type: 'disableRule', rule: r.id }); toast.show(`${r.id} 해제 · 이 범위는 다시 교차검증으로`) }
  const cols: Column<AutoRule>[] = [
    { key: 'id', header: '기준', render: (r) => <span className="mono">{r.id}</span> },
    { key: 'what', header: '내용', render: (r) => <>{TARGET_LABEL[r.action]} · {ruleText(r)}</> },
    { key: 'status', header: '상태', render: (r) => <State v={r.status} /> },
    { key: 'approver', header: '넓히기 승인', render: (r) => <span className="small">{r.approver}</span> },
    { key: 'basis', header: '관찰 통과 머지', render: (r) => <>{r.basis.map((b, i) => <span key={b}>{i > 0 && ' · '}<Ref s={s} id={b} /></span>)}</> },
    { key: 'bt', header: '백테스트', render: (r) => <span className="small">과거 {r.backtest.checked}건 대조 · 기준에 드는 {r.backtest.covered}건 · 충돌 {r.backtest.conflicts.length}건</span> },
    { key: 't', header: '만든 시각', render: (r) => <span className="mono small">{r.t}</span> },
    { key: 'off', header: '', render: (r) => r.status === '정식' ? <Button size="small" onClick={() => off(r)}>기준 끄기</Button> : <span className="muted small">{r.offWhy ?? r.decideWhy}</span> },
  ]
  return (
    <>
      <Lead>머지 → {OBSERVE_MIN}분 관찰(장애·revert 없음) → 같은 경로 {OBSERVE_N}건 → 기준 제안·백테스트 → 안전 장치({GUARD.lines}줄·{GUARD.files}파일) 안 재무 AI / 밖 사람(태윤) 승인 → 정식. 장애 연결·사람이 끄면 즉시 해제.</Lead>
      <Stats items={[
        { label: '정식 기준', value: by('정식') },
        { label: '관찰 중', value: pol.observing.filter((o) => o.result === '관찰 중').length, hint: `머지 뒤 ${OBSERVE_MIN}분` },
        { label: '승인 대기', value: by('승인 대기'), hint: '재무 AI · 사람' },
        { label: '해제됨', value: by('해제됨') },
        { label: '기준으로 자동 머지 / 장애 연결', value: `${pol.history.length} / ${linked}`, hint: '건' },
      ]} />
      <Card as="section" title="학습된 기준" sub="이 범위 안의 PR은 검수 통과 뒤 사람 클릭 없이 머지된다">
        <LinkTable columns={cols} rows={pol.rules.slice().reverse()} rowKey={(r) => r.id} rowHref={(r) => hrefOf(s, r.basis[0])} rowTitle="이 기준을 배운 건 보기" highlight={(r) => r.id === item} />
      </Card>
      <div className="auto-grid">
        <Card as="section" title="되돌릴 수 없는 일" sub="교차검증 대신 항상 사람이 확인한다">
          <ul className="levels">
            {IRREVERSIBLE.map((x) => <li key={x.id}><b>{x.label}</b> <span className="muted small">{x.words.join(', ')}</span></li>)}
          </ul>
        </Card>
        <Card as="section" title="변경 기록" sub="누가(자동·교차검증·사람) 언제 바꿨나">
          <ul className="levels">
            {pol.log.slice().reverse().map((x, i) => (
              <li key={i}><Tag tone={x.by === '자동' ? 'amber' : undefined}>{x.by}</Tag> <span className="muted small mono">{x.t}</span> <Linked s={s} text={x.text} /></li>
            ))}
          </ul>
        </Card>
      </div>
      <Card as="section" title="기준으로 자동 머지한 이력" sub="학습된 기준으로 자동 머지한 PR과 그 뒤 결과">
        {pol.history.length ? (
          <LinkTable
            rows={pol.history.slice().reverse()}
            rowKey={(h) => h.pr}
            rowHref={(h) => `#/work/prs/${prSlug(h.pr)}`}
            rowTitle="PR과 코드 역추적 보기"
            columns={[
              { key: 't', header: '시각', render: (h) => <span className="mono small">{h.t}</span> },
              { key: 'pr', header: 'PR', render: (h) => <span className="mono">{h.pr}</span> },
              { key: 'req', header: '요청', render: (h) => <Ref s={s} id={h.req} /> },
              { key: 'rule', header: '기준', render: (h) => (h.rule ? <Ref s={s} id={h.rule} /> : '-') },
              { key: 'size', header: '변경', render: (h) => `${h.lines}줄·${h.files}파일` },
              { key: 'result', header: '결과', render: (h) => <State v={h.result === '정상' ? '완료' : '장애 연결'} /> },
            ]}
          />
        ) : <div className="muted small">아직 없습니다. 다른 부서 화면에서 "간단 요청 보내기"를 눌러 보세요.</div>}
      </Card>
    </>
  )
}

// ---------------------------------------------------------------- AI 승인 감사 (사람 사후 감사)
export function Audit({ s, d }: P) {
  const toast = useToast()
  const today = s.audit.filter((x) => x.t.startsWith(TODAY))
  const n = (k: AuditItem['kind']) => today.filter((x) => x.kind === k).length
  const live = new Set(activeRules(s.autoPolicy).map((r) => r.id))
  const cols: Column<AuditItem>[] = [
    { key: 't', header: '시각', render: (x) => <span className="mono small">{x.t.slice(6)}</span> },
    { key: 'kind', header: 'AI가 한 일', render: (x) => <State v={x.kind} /> },
    { key: 'ref', header: '대상', render: (x) => <><Ref s={s} id={x.ref} /> <span className="small">{x.what}</span></> },
    { key: 'basis', header: '어떤 기준으로', render: (x) => <span className="small">{x.rule && x.rule !== x.ref && <><Ref s={s} id={x.rule} /> · </>}<Linked s={s} text={x.basis} /></span> },
    { key: 'verifier', header: '검증자', render: (x) => <span className="small">{x.verifier}</span> },
    { key: 'result', header: '결과', render: (x) => <span className="small">{x.result}</span> },
    { key: 'off', header: '', render: (x) => x.rule && live.has(x.rule) ? <Button size="small" onClick={() => { d({ type: 'disableRule', rule: x.rule! }); toast.show(`${x.rule} 해제`) }}>이 기준 끄기</Button> : null },
  ]
  return (
    <>
      <Lead>오늘 AI가 사람 클릭 없이 승인·처리한 건. 사전 승인 대신 사후 감사.</Lead>
      <Stats items={[
        { label: '자동 확정', value: n('자동 확정'), hint: '기획서' },
        { label: '자동 머지', value: n('자동 머지'), hint: '학습된 기준으로' },
        { label: '교차검증 승인', value: n('교차검증 승인'), hint: `거절 ${n('교차검증 거절')}`, href: '#/work/reviews' },
        { label: '정식이 된 기준', value: n('기준 정식'), hint: `제안 ${n('기준 제안')} · 거부 ${n('학습 거부')}`, href: '#/metrics/autonomy' },
      ]} />
      <Card as="section" title="건별 기록" sub={`오늘 ${today.length}건 · 최근 순`}>
        {today.length
          ? <LinkTable columns={cols} rows={today.slice().reverse()} rowKey={(x) => `${x.t}|${x.kind}|${x.ref}`} rowHref={(x) => hrefOf(s, x.ref)} rowTitle="이 건 보기" />
          : <div className="empty"><b>오늘 AI가 스스로 처리한 건이 없습니다</b></div>}
      </Card>
    </>
  )
}

// ---------------------------------------------------------------- 지표 (모두 코드로 계산, 각 숫자는 원천 화면으로)
export function Metrics({ s }: { s: S }) {
  const prs = s.prs
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : '-')
  const avgPr = s.firstPrMinutes.length ? `${Math.round(s.firstPrMinutes.reduce((a, b) => a + b, 0) / s.firstPrMinutes.length)}분` : '-'
  const human = s.specs.reduce((a, x) => a + x.humanSentences, 0) + s.threads.flatMap((t) => t.messages).filter((m) => m.dept !== 'planning').length
  const today = s.audit.filter((x) => x.t.startsWith(TODAY))
  const n = (k: AuditItem['kind']) => today.filter((x) => x.kind === k).length
  return (
    <div className="flow-col">
      <section className="flow-sec">
        <h2>기획·개발</h2>
        <Stats items={[
          { label: '요청 → 첫 PR', value: avgPr, hint: '평균', href: '#/work/prs' },
          { label: '완료 기준 일치', value: pct(prs.filter((p) => p.reviews[p.reviews.length - 1] === '통과').length, prs.length), hint: `PR ${prs.length}`, href: '#/work/prs' },
          { label: '검수 1회 통과', value: pct(prs.filter((p) => p.reviews[0] === '통과').length, prs.length), hint: `PR ${prs.length}`, href: '#/work/prs' },
          { label: '사람이 쓴 문장', value: human, href: '#/talk' },
          { label: '기각 안 경고', value: s.warnings, hint: 'check_plan', href: '#/work/specs/SPEC-7/R2' },
          { label: '초안 채택', value: pct(s.draftsAccepted, s.draftsIn), hint: `초안 ${s.draftsIn}`, href: '#/requests/incoming' },
        ]} />
      </section>
      <section className="flow-sec">
        <h2>자동 처리 · 오늘</h2>
        <Stats items={[
          { label: '자동 확정', value: n('자동 확정'), href: '#/metrics/audit' },
          { label: '자동 머지', value: n('자동 머지'), href: '#/metrics/audit' },
          { label: '교차검증 대기', value: s.reviews.filter((x) => !x.result).length, href: '#/work/reviews' },
          { label: '교차검증 승인 / 거절', value: `${n('교차검증 승인')} / ${n('교차검증 거절')}`, href: '#/metrics/audit' },
          { label: '사용 중 기준', value: activeRules(s.autoPolicy).length, href: '#/metrics/autonomy' },
        ]} />
      </section>
    </div>
  )
}

// ---------------------------------------------------------------- 운영·배포 (머지된 PR 시간순)
export function Ops({ s, item }: { s: S; item?: string }) {
  const req = s.requests.find((r) => r.id === item)
  const specIds = req ? s.specs.filter((x) => x.id === req.spec || x.request === req.id).map((x) => x.id) : []
  const prs = req ? s.prs.filter((p) => s.tasks.some((t) => t.id === p.task && specIds.includes(t.spec))) : s.prs
  const merged = prs.filter((p) => p.merged)
  const open = prs.filter((p) => !p.merged)
  const incidentOf = (pr: string) => s.incidents.find((i) => i.candidates.some((c) => c.pr === pr))
  const mergedAt = (id: string) => s.activity.filter((a) => a.text.startsWith(`${id} `) && (a.point === 'github_merge_pull' || a.point === 'execute(merge_pr)')).pop()?.t ?? s.incidents.flatMap((i) => i.candidates).find((c) => c.pr === id)?.mergedAt ?? '-'
  return (
    <div className="flow-col">
      {req && <Notice icon="bell"><Ref s={s} id={req.id} /> {req.title} · <a href="#/work/ops">전체</a> · <a href={`#/projects/${req.id}`}>프로젝트</a></Notice>}
      <Stats items={[
        { label: '배포된 PR', value: merged.length },
        { label: '자동 머지', value: merged.filter((p) => p.auto).length },
        { label: '사람 머지', value: merged.filter((p) => !p.auto).length },
        { label: '머지 전', value: open.length, href: '#/work/prs' },
        { label: '장애 연결', value: merged.filter((p) => incidentOf(p.id)).length, href: '#/work/aftercare' },
      ]} />
      <Flow label="배포 이력">
        {merged.slice().reverse().map((p) => {
          const i = incidentOf(p.id)
          return (
            <Step key={p.id} name={mergedAt(p.id).slice(6) || '-'} on={!i} href={`#/work/prs/${prSlug(p.id)}`} title={<>{p.id} · {p.title.replace(/^[A-Z]\d+: /, '')}</>}>
              <Pairs items={[
                ['머지', mergedLabel(p)],
                ['관문', `${p.gate.result} · ${p.gate.lines}줄·${p.gate.files}파일 · ${p.gate.paths}`],
                ['장애', i ? <Ref s={s} id={i.ctx} /> : '없음'],
              ]} />
            </Step>
          )
        })}
      </Flow>
    </div>
  )
}

// ---------------------------------------------------------------- 사후관리 (DejaFix 연동). 사건마다 세로 흐름
export function Aftercare({ s, item }: { s: S; item?: string }) {
  useEffect(() => {
    if (item) document.getElementById(`inc-${item}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [item])
  const acts = s.activity.filter((a) => a.ctx.startsWith('ctx_inc_'))
  return (
    <div className="flow-col">
      {s.incidents.map((i) => {
        const follow = s.requests.find((r) => r.id === i.followUp)
        const c0 = i.candidates[0]
        return (
          <section key={i.ctx} id={`inc-${i.ctx}`} className={`flow-sec${item === i.ctx ? ' card-focus' : ''}`}>
            <Head kicker={<><a href={convoHref(i.ctx)} className="mono plain-link">{i.ctx}</a> · {i.at}</>} title={i.title} side={<State v={i.linked ? '완료' : '접수'} />} />
            <Flow>
              <Step name="개시" on href={convoHref(i.ctx)} title={`${i.at} · 통합 에이전트`} />
              <Step name="후보" on={i.candidates.length > 0} title={i.candidates.map((c) => c.pr).join(', ') || '없음'}>
                <ul>{i.candidates.map((c) => <li key={c.pr}><Ref s={s} id={c.pr} /> · <a href={`#/work/specs/${c.spec}/${c.para}`} className="plain-link">{c.spec} {c.para}</a> · {c.mergedAt} 머지</li>)}</ul>
              </Step>
              <Step name="롤백" on={!!i.rollbackCheck} title={i.rollbackCheck ? `${i.rollbackCheck.result} · ${i.rollbackCheck.target}` : '없음'}>
                {i.rollbackCheck && <p className="muted">{i.rollbackCheck.why}</p>}
              </Step>
              <Step name="문단" on={i.linked} href={c0 ? `#/work/specs/${c0.spec}/${c0.para}` : undefined} title={i.candidates.map((c) => `${c.spec} ${c.para}`).join(', ') || '-'} />
              <Step name="후속" on={!!follow} href={follow ? hrefOf(s, follow.id) : undefined} title={follow ? `${follow.id} · ${follow.state}` : '없음'}>
                {follow && <p>{follow.title}</p>}
              </Step>
            </Flow>
          </section>
        )
      })}
      <details className="proj-activity">
        <summary>사건 활동 {acts.length}</summary>
        <ActivityList s={s} items={acts.slice().reverse()} />
      </details>
    </div>
  )
}

// 부서별로 연결 지점을 묶는다: 부서 → 연결 지점 → 건수
function byPoint(items: Act[]) {
  const m = new Map<string, Act[]>()
  for (const a of items) m.set(`${a.from === 'planning' ? '보냄' : '받음'}|${a.point}`, [...(m.get(`${a.from === 'planning' ? '보냄' : '받음'}|${a.point}`) ?? []), a])
  return Array.from(m, ([k, v]) => ({ dir: k.split('|')[0], point: k.split('|').slice(1).join('|'), items: v })).sort((a, b) => b.items.length - a.items.length)
}

// ---------------------------------------------------------------- 에이전트 활동 (부서 → 연결 지점 → 건수)
export function Activity({ s, item }: { s: S; item?: string }) {
  const peers: Dept[] = Array.from(new Set(s.activity.flatMap((a) => [a.from, a.to]).filter((x) => x !== 'planning')))
  const peer = (item && peers.includes(item as Dept) ? item : 'all') as Dept | 'all'
  const of = (x: Dept | 'all') => s.activity.filter((a) => x === 'all' || a.from === x || a.to === x)
  const rows = of(peer)
  const list = <ListPane groups={[{ label: '부서', rows: [
    { key: 'all', href: '#/metrics/activity', on: peer === 'all', title: '전체', meta: <>{s.activity.length}건</> },
    ...peers.map((x) => ({ key: x, href: `#/metrics/activity/${x}`, on: peer === x, title: DEPT_NAME[x], meta: <>{of(x).length}건</> })),
  ] }]} />
  return (
    <Layout
      list={list}
      head={<Head kicker={`활동 ${rows.length}건`} title={peer === 'all' ? '전체 부서' : DEPT_NAME[peer]} />}
      below={
        <details className="proj-activity">
          <summary>활동 로그 {rows.length}</summary>
          <ActivityList s={s} items={rows.slice().reverse()} />
        </details>
      }
    >
      <Flow label="연결 지점">
        {byPoint(rows).map((g) => (
          <Step key={`${g.dir}|${g.point}`} name={g.dir} on title={<><Point>{g.point}</Point> · {g.items.length}건</>}>
            {g.items.slice(-2).reverse().map((a, i) => <p key={i}><span className="mono muted">{a.t.slice(6)}</span> <Dep d={a.from} /> → <Dep d={a.to} /> · <Linked s={s} text={a.text} /></p>)}
          </Step>
        ))}
      </Flow>
    </Layout>
  )
}

// ---------------------------------------------------------------- 부서 연동 (부서별 연결 지점 · 오늘 호출 수)
export function Links({ s, d }: P) {
  const count = (p: (typeof PORTS)[number]) => s.activity.filter((a) => a.point.startsWith(p.point.split(' ')[0]) && (p.dir === 'in' ? a.from === p.peer || (p.peer === 'support' && a.from === 'marketing') : a.to === p.peer)).length
  const peers = Array.from(new Set(PORTS.map((p) => p.peer)))
  return (
    <div className="flow-col">
      <SendWindow d={d} />
      <div className="peer-grid">
        {peers.map((peer) => {
          const ports = PORTS.filter((p) => p.peer === peer)
          return (
            <Side key={peer} title={DEPT_NAME[peer]} href={`#/metrics/activity/${peer}`} sum={`연결 ${ports.length} · 오늘 ${ports.reduce((n, p) => n + count(p), 0)}건`}>
              <ul className="peer-rows">
                {ports.map((p) => (
                  <li key={`${p.dir}|${p.point}`} title={p.what}>
                    <span><Point>{p.point}</Point>{p.priority === 'required' && <> <Tag tone="green">필수</Tag></>}</span>
                    <span className="muted">{p.dir === 'in' ? '받음' : '보냄'}</span>
                    <span><Grade g={p.grade} /></span>
                    <span className="num">{count(p)}</span>
                  </li>
                ))}
              </ul>
            </Side>
          )
        })}
      </div>
      <details className="proj-activity">
        <summary>전체 활동 로그 {s.activity.length}</summary>
        <ActivityList s={s} items={[...s.activity].reverse()} />
      </details>
    </div>
  )
}
