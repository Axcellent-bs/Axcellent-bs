// 프로젝트별 보기: 변경 요청 1건(context_id 하나)을 프로젝트 하나로 본다. 왼쪽은 프로젝트 목록.
// 가운데는 순서대로 진행되는 요청 → 작업 → 운영 → 처리를 세로 흐름으로, 오른쪽은 아무 때나 생기는 소통·문서.
// 단계 한 줄·곁 블록은 통째로 눌러 그 내용을 다루는 화면(해당 프로젝트 항목)으로 간다.
import type { Dispatch } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, type Action, type Request, type State as S } from './data'
import { ActivityList, Dep, State, Tag } from './parts'
import { Linked, Ref, convoHref, hrefOf, prSlug, reviewOf } from './links'
import { Flow, Head, Layout, ListPane, Side, Step, mergedLabel, prStatus, specStatus } from './flow'

// 프로젝트 = 원본 요청 (같은 요청으로 연결만 한 것은 원본에 붙인다)
export const projectsOf = (s: S) => s.requests.filter((r) => r.state !== '중복 연결')

export function gather(s: S, r: Request) {
  const spec = s.specs.find((x) => x.id === r.spec || x.request === r.id)
  const tasks = spec ? s.tasks.filter((t) => t.spec === spec.id) : []
  const prs = s.prs.filter((p) => tasks.some((t) => t.id === p.task))
  const incidents = s.incidents.filter((i) => i.candidates.some((c) => prs.some((p) => p.id === c.pr)) || i.ctx === r.ctx)
  const dups = s.requests.filter((x) => x.duplicateOf === r.id)
  const follow = s.requests.filter((x) => incidents.some((i) => i.followUp === x.id) && x.id !== r.id)
  const ids = [r.id, spec?.id].filter(Boolean) as string[]
  const threads = s.threads.filter((t) => (spec && t.on === spec.id))
  const mentions = s.threads.filter((t) => t.kind !== 'spec' && t.on !== spec?.id)
    .flatMap((t) => t.messages.filter((m) => ids.some((id) => m.text.includes(id))).map((m) => ({ ...m, ch: t.title })))
  const decisions = spec ? s.decisions.filter((x) => x.spec === spec.id) : []
  const docs = s.docs.filter((d) => d.about === r.id || d.blocks.some((b) => ids.some((id) => b.text.includes(id))))
  // 이 요청의 context_id + 이 프로젝트 PR이 연결된 장애 사건의 context_id
  const activity = s.activity.filter((a) => a.ctx === r.ctx || incidents.some((i) => i.ctx === a.ctx))
  const replies = activity.filter((a) => a.from === 'planning' && a.to !== 'planning' && a.to !== 'safety' && a.to !== 'shop')
  return { spec, tasks, prs, incidents, dups, follow, threads, mentions, decisions, docs, activity, replies }
}

// 처리 단계 값: 회신 완료 / 장애 연결 / 처리 전
export const doneLabel = (r: Request, g: ReturnType<typeof gather>) => r.state === '완료' ? (g.incidents.length ? '회신 완료 · 장애 연결' : '회신 완료') : r.state === '중복 연결' ? `${r.duplicateOf} 연결` : g.incidents.length ? '장애 연결' : '처리 전'

export function Projects({ s, d, sel }: { s: S; d: Dispatch<Action>; sel?: string }) {
  const toast = useToast()
  const list = projectsOf(s)
  const order = (r: Request) => (r.state === '작업 중' || r.state === '기획 중' ? 0 : r.state === '접수' ? 1 : 2)
  const sorted = list.slice().sort((a, b) => order(a) - order(b))
  const r = list.find((x) => x.id === sel) ?? sorted[0]
  if (!r) return null
  const g = gather(s, r)
  const talks = g.threads.reduce((n, t) => n + t.messages.length, 0) + g.mentions.length
  const groups = [
    { label: '진행 중', rows: sorted.filter((x) => order(x) === 0) },
    { label: '접수', rows: sorted.filter((x) => order(x) === 1) },
    { label: '완료', rows: sorted.filter((x) => order(x) === 2) },
  ].map((gr) => ({ label: gr.label, rows: gr.rows.map((x) => ({ key: x.id, href: `#/projects/${x.id}`, on: x.id === r.id, title: x.title, meta: <>{DEPT_NAME[x.from]} <State v={x.state} /></> })) }))

  return (
    <Layout
      list={<ListPane groups={groups} />}
      head={<Head kicker={<>{r.id} · {DEPT_NAME[r.from]}</>} title={r.title} side={<><State v={r.state} /><Button size="small" onClick={() => { d({ type: 'gatherDoc', about: r.id }); toast.show(`${r.id} 정리 문서를 만들었습니다`) }}>문서로 정리</Button></>} />}
      side={
        <>
          <Side title="소통" href={g.threads[0] ? `#/talk/${g.threads[0].id}` : convoHref(r.ctx)} sum={`대화 ${talks} · 결정 ${g.decisions.length}`}>
            {g.threads.flatMap((t) => t.messages.slice(-2).map((m, i) => <p key={`${t.id}-${i}`} className="pside-msg"><b>{m.who}</b> <Linked s={s} text={m.text} /></p>))}
            {g.decisions.map((x) => <p key={x.id} className="pside-msg"><Ref s={s} id={x.id} /> {x.text}</p>)}
            <a href={convoHref(r.ctx)} className="pside-link">에이전트 대화 {g.activity.length}</a>
          </Side>
          <Side title="문서" href={g.docs.length ? `#/docs/${g.docs[g.docs.length - 1].id}` : undefined} sum={`정리 문서 ${g.docs.length}`}>
            {g.docs.map((x) => <p key={x.id} className="pside-msg"><Ref s={s} id={x.id} /> {x.title}</p>)}
          </Side>
        </>
      }
      below={
        <details className="proj-activity">
          <summary>에이전트 활동 {g.activity.length}</summary>
          <ActivityList s={s} items={g.activity.slice().reverse()} compact />
        </details>
      }
    >
      <Flow>
        <Step name="요청" on href={hrefOf(s, r.id)} title={DEPT_NAME[r.from]}>
          <p><Linked s={s} text={r.evidence} /></p>
          <p className="muted">{r.via}{r.auto?.ok ? ' · 간단 요청' : ''}</p>
          {g.dups.map((x) => <p key={x.id} className="muted">같은 요청 <Ref s={s} id={x.id} /> · {DEPT_NAME[x.from]}</p>)}
        </Step>

        <Step name="기획" on={g.tasks.length > 0} href={g.spec ? `#/work/specs/${g.spec.id}` : hrefOf(s, r.id)} title={g.spec ? `${g.spec.id} · ${specStatus(s, g.spec)} · 작업 ${g.tasks.length}` : '기획서 없음'}>
          {g.spec && (
            <ul>
              {g.spec.paras.map((p) => {
                const ts = g.tasks.filter((t) => t.para === p.id)
                return (
                  <li key={p.id}>
                    <a href={`#/work/specs/${g.spec!.id}/${p.id}`} className="plain-link">{p.id} {p.text}</a>
                    {p.warn && <> <Tag tone="amber">경고 {p.warn.card}</Tag></>}
                    {p.incident && <> <Tag tone="amber">장애 {p.incident}</Tag></>}
                    {ts.map((x) => <div key={x.id} className="muted">{x.id} · {x.state}</div>)}
                  </li>
                )
              })}
            </ul>
          )}
          {g.spec && reviewOf(s, g.spec.id) && <p><Ref s={s} id={reviewOf(s, g.spec.id)!.id} /> · {reviewOf(s, g.spec.id)!.verifier}</p>}
        </Step>

        <Step name="운영" on={g.prs.some((p) => p.merged)} href={g.prs.length ? `#/work/ops/${r.id}` : undefined} title={g.prs.length ? prStatus(s, g.prs) : 'PR 없음'}>
          {g.prs.length > 0 && (
            <ul>
              {g.prs.map((p) => {
                const xr = reviewOf(s, p.id)
                return (
                  <li key={p.id}>
                    <a href={`#/work/prs/${prSlug(p.id)}`} className="plain-link">{p.title.replace(/^[A-Z]\d+: /, '')}</a>
                    <div className="muted">{p.id} · 검수 {p.reviews[p.reviews.length - 1]} · {xr ? `${xr.lane === 'human' ? '사람 확인' : '교차검증'} 대기 ${xr.id}` : mergedLabel(p)} · {p.gate.lines}줄 {p.gate.files}파일</div>
                  </li>
                )
              })}
            </ul>
          )}
        </Step>

        <Step name="처리" on={r.state === '완료'} href={g.incidents[0] ? hrefOf(s, g.incidents[0].ctx) : g.replies.length ? convoHref(r.ctx) : undefined} title={doneLabel(r, g)}>
          {(g.replies.length > 0 || g.incidents.length > 0 || g.follow.length > 0) && (
            <ul>
              {g.replies.slice(-3).map((a, i) => <li key={i}><Dep d={a.to} /> · <Linked s={s} text={a.text} /></li>)}
              {g.incidents.map((i) => (
                <li key={i.ctx}>
                  장애 <Ref s={s} id={i.ctx} className="plain-link">{i.title}</Ref>
                  {i.rollbackCheck && <div className="muted">롤백 검증 {i.rollbackCheck.result} · {i.rollbackCheck.why}</div>}
                </li>
              ))}
              {g.follow.map((x) => <li key={x.id}>후속 <Ref s={s} id={x.id} /> {x.title}</li>)}
            </ul>
          )}
        </Step>
      </Flow>
    </Layout>
  )
}
