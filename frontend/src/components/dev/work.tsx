// 개발 작업·PR과 AI 교차검증. 프로젝트 탭과 같은 틀(왼쪽 목록 / 가운데 세로 흐름 / 오른쪽 곁 블록)
import { useEffect, useState, type Dispatch, type ReactNode } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { CROSS_CHECK_TIMEOUT_MIN, TARGET_LABEL, OBSERVE_MIN, type Action, type CrossReview, type PR, type State as S } from '@/lib/planningData'
import { codeOf, traceOf, type CodeLine } from '@/components/dev/trace'
import { DiffFiles } from '@/components/dev/diff'
import { DEV_API, repoApi, type RepoFile } from '@/lib/planning'
import { State, Tag } from '@/components/planning-common/parts'
import { Linked, Ref, convoHref, hrefOf, prFromSlug, prSlug, reviewOf, toPath } from '@/components/planning-common/links'
import { Flow, Head, Layout, ListPane, Pairs, Side, Step, mergedLabel } from '@/components/planning-common/flow'
import { RepoTargetBlock } from '@/components/planning-common/repo'
import { CodingLabel, CodingStep, DevTeamTag, codingState, fromDevTeam, isRunning } from '@/components/dev/coding'

interface P { s: S; d: Dispatch<Action> }

// ---------------------------------------------------------------- 작업·PR (왼쪽 작업 목록 / 가운데 PR 흐름 / 오른쪽 근거 체인)
const waitLabel = (x: CrossReview) => (x.lane === 'human' ? '사람 확인 대기' : '교차검증 대기')

// 대기 시작부터 지난 분. 서버 건은 ISO(at), 예시 건은 "MM-DD HH:mm"(KST)
function waitedMin(x: CrossReview, now: number): number | undefined {
  const m = x.t.match(/^(\d{2})-(\d{2}) (\d{2}):(\d{2})$/)
  const start = x.at ? Date.parse(x.at) : m ? Date.parse(`${new Date(now).getFullYear()}-${m[1]}-${m[2]}T${m[3]}:${m[4]}:00+09:00`) : NaN
  return Number.isNaN(start) ? undefined : Math.max(0, Math.floor((now - start) / 60_000))
}
function useNow(ms = 30_000) {
  const [t, setT] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setT(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return t
}

// 교차검증 건의 바뀐 파일. 레포에 열린 PR이면 레포 diff를, 예시 PR이면 바뀐 대표 줄로 만든 diff를 보여 준다
function exampleFiles(s: S, pr: PR): RepoFile[] {
  const by = new Map<string, CodeLine[]>()
  for (const c of codeOf(s, pr)) by.set(c.path, [...(by.get(c.path) ?? []), c])
  return [...by].map(([path, ls]) => ({
    path, status: 'modified', additions: ls.length, deletions: 0, previous_path: null, truncated: false,
    patch: [`@@ -${ls[0].line},0 +${ls[0].line},${ls.length} @@`, ...ls.map((l) => `+${l.text}`)].join('\n'),
  }))
}
function Changes({ s, pr }: { s: S; pr: PR }) {
  const conn = pr.target?.connectionId
  const n = pr.vcsNumber ?? undefined
  const [files, setFiles] = useState<RepoFile[] | null>(n != null && conn ? null : exampleFiles(s, pr))
  const [err, setErr] = useState(false)
  useEffect(() => {
    if (n == null || !conn) return
    let alive = true
    repoApi(DEV_API, conn).pr(n).then((x) => alive && setFiles(x.diff.files)).catch(() => alive && setErr(true))
    return () => { alive = false }
  }, [conn, n])
  const repoLink = n != null ? <a href={toPath(`work/prs/repo/${n}/files`)} className="plain-link small">레포 PR #{n} 변경 파일</a> : null
  if (err) return <p className="muted small">레포 diff를 읽지 못했어요 {repoLink}</p>
  if (!files) return <p className="muted small">읽는 중</p>
  return (
    <>
      {repoLink}
      <DiffFiles files={files} draftKey={n != null && conn ? `${conn}#${n}` : pr.id} editable={!pr.merged} compact />
    </>
  )
}

// 판단 전: 무엇을 기다리는지 · 얼마나 · 언제 사람에게 넘어가는지 · 지금 사람이 할 수 있는 것
function Waiting({ s, d, r }: P & { r: CrossReview }) {
  const toast = useToast()
  const now = useNow()
  const waited = waitedMin(r, now)
  const left = waited === undefined ? undefined : CROSS_CHECK_TIMEOUT_MIN - waited
  const open = r.checks.filter((c) => c.result !== '통과').map((c) => c.label)
  const prHref = r.target === 'merge_pr' ? toPath(`work/prs/${prSlug(r.ref)}`) : hrefOf(s, r.ref)
  return (
    <>
      <Pairs items={[
        ['기다리는 것', r.lane === 'ai' ? `${r.verifier} 판정` : r.irreversible ? `사람 확인 · ${r.irreversible}` : '사람 확인'],
        ['대기', waited === undefined ? r.t : `${r.t}부터 · ${waited}분`],
        ...(r.lane === 'ai' ? [['사람 확인으로', left === undefined ? `${CROSS_CHECK_TIMEOUT_MIN}분 뒤 자동` : left > 0 ? `${left}분 뒤 자동` : '곧 넘어감'] as [string, string]] : []),
        ...(open.length ? [['걸린 항목', open.join(' · ')] as [string, string]] : r.routeWhy.length ? [['걸린 항목', r.routeWhy.join(' · ')] as [string, string]] : []),
      ]} />
      <div className="actions">
        <Button variant="primary" onClick={() => { d({ type: 'humanDecide', review: r.id, ok: true }); toast.show(`${r.ref} 진행`) }}>확인하고 진행</Button>
        {r.lane === 'human' && <Button onClick={() => { d({ type: 'humanDecide', review: r.id, ok: false }); toast.show(`${r.ref} 보류`) }}>보류</Button>}
        {prHref && <a href={prHref} className="btn-quiet px-3 py-1">{r.target === 'merge_pr' ? 'PR에서 수정 요청' : '기획서 보기'}</a>}
      </div>
    </>
  )
}
export function Work({ s, d, item }: P & { item?: string }) {
  const [line, setLine] = useState<CodeLine>()
  useEffect(() => { setLine(undefined) }, [item])
  const prOf = (taskId: string) => s.prs.find((p) => p.task === taskId)
  // 주소가 작업 id면 그 작업의 PR, PR이 아직 없으면 작업만 보여 준다 (직접 개발 직후)
  const byTask = s.tasks.find((t) => t.id === item)
  const taskOnly = byTask && !prOf(byTask.id) ? byTask : undefined
  const sel = (byTask && prOf(byTask.id)) ?? s.prs.find((p) => p.id === prFromSlug(item)) ?? s.prs.find((p) => p.target?.status === 'proposed') ?? s.prs.find((p) => reviewOf(s, p.id)) ?? s.prs[s.prs.length - 1]
  const groupOf = (taskId: string, state: string) => { const p = prOf(taskId); return state === '완료' || p?.merged ? 2 : p && reviewOf(s, p.id) ? 1 : 0 }
  const tasks = s.tasks.slice().reverse()
  const row = (t: S['tasks'][number]) => {
    const p = prOf(t.id)
    const xr = p && reviewOf(s, p.id)
    const coding = t.coding && !p?.merged && !p?.target && !xr ? codingState(t.coding) : undefined
    return {
      key: t.id,
      href: p ? toPath(`work/prs/${prSlug(p.id)}`) : t.coding ? toPath(`work/prs/${encodeURIComponent(t.id)}`) : toPath(`work/specs/${t.spec}/${t.para}`),
      on: taskOnly ? t.id === taskOnly.id : !!p && p.id === sel?.id,
      title: t.title,
      meta: <>{t.id}{p ? ` · ${p.id}` : ''} <State v={p?.target?.status === 'proposed' ? '레포 승인 대기' : xr ? waitLabel(xr) : coding ?? t.state} live={!!coding && !!t.coding && isRunning(t.coding.status)} />{fromDevTeam(s, t) && <> <DevTeamTag /></>}</>,
    }
  }
  const list = <ListPane empty="작업 없음" groups={(['진행 중', '교차검증·사람 확인', '완료'] as const).map((label, i) => ({ label, rows: tasks.filter((t) => groupOf(t.id, t.state) === i).map(row) }))} />
  if (taskOnly) {
    const text = s.specs.find((x) => x.id === taskOnly.spec)?.paras.find((p) => p.id === taskOnly.para)?.text
    return (
      <Layout list={list} head={<Head kicker={<>{taskOnly.id} · {taskOnly.spec}{fromDevTeam(s, taskOnly) && <> · <DevTeamTag /></>}</>} title={taskOnly.title} side={taskOnly.coding ? <CodingLabel c={taskOnly.coding} /> : <State v={taskOnly.state} />} />}>
        <Flow>
          <Step name="작업" on href={toPath(`work/specs/${taskOnly.spec}/${taskOnly.para}`)} title={`${taskOnly.id} · ${taskOnly.spec} ${taskOnly.para}`}>
            {text && <p>{text}</p>}
          </Step>
          <CodingStep task={taskOnly} />
          <Step name="PR" on={false} title="PR 전" />
        </Flow>
      </Layout>
    )
  }
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
      head={<Head kicker={<>{sel.id} · {task?.id} · <span className="mono">{sel.branch}</span>{sel.vcsNumber != null && <> · <a href={toPath(`work/prs/repo/${sel.vcsNumber}`)} className="plain-link">레포 #{sel.vcsNumber}</a></>}{fromDevTeam(s, task) && <> · <DevTeamTag /></>}</>} title={sel.title.replace(/^[A-Z]\d+: /, '')} side={<State v={sel.merged ? '완료' : xr ? waitLabel(xr) : last === '불일치' || rejected ? '불일치' : '머지 대기'} />} />}
      side={
        <Side title="근거 체인" sum={`${chain.length}단계`}>
          {chain.map((x, i) => <p key={i} className="pside-msg"><b>{x.kind}</b> {x.href ? <a href={x.kind === '문단' && spec && para ? toPath(`work/specs/${spec.id}/${para.id}`) : toPath(x.href)} className="plain-link">{x.title}</a> : x.title}</p>)}
        </Side>
      }
      below={<Trace s={s} pr={sel} line={line} setLine={setLine} />}
    >
      <Flow>
        <Step name="작업" on href={task ? toPath(`work/specs/${task.spec}/${task.para}`) : undefined} title={task ? `${task.id} · ${task.spec} ${task.para}` : '-'}>
          {para && <p>{para.text}</p>}
          {para?.warn && <Tag tone="amber">경고 {para.warn.card}</Tag>}
          {para?.incident && <Tag tone="amber">장애 {para.incident}</Tag>}
        </Step>
        {task && <CodingStep task={task} />}
        {sel.target && (
          <Step name="대상 레포" on={sel.target.status === 'approved'} title={sel.target.status === 'approved' ? sel.target.repo : sel.target.status === 'rejected' ? '거절됨' : '승인 대기'}>
            <RepoTargetBlock key={sel.id} p={sel} d={d} />
          </Step>
        )}
        <Step name="관문" on title={`${sel.gate.result} · ${sel.gate.lines}줄·${sel.gate.files}파일 · ${sel.gate.paths}`} />
        <Step name="검수" on={last === '통과'} title={sel.reviews.join(' → ')}>
          {!sel.merged && (last === '불일치' || rejected) && <Button size="small" onClick={() => d({ type: 'reviewPr', pr: sel.id })}>다시 검수</Button>}
        </Step>
        <Step name="머지" on={sel.merged} href={sel.url || (xr ? toPath(`work/reviews/${xr.id}`) : undefined)} title={sel.merged ? mergedLabel(sel) : xr ? `${waitLabel(xr)} · ${xr.id}` : rejected ? '검증자 거절' : '머지 전'}>
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
                <div>{x.href ? <a href={toPath(x.href)}>{x.title}</a> : <b>{x.title}</b>}</div>
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
  const all = s.reviews.slice().reverse()
  const r = s.reviews.find((x) => x.id === item) ?? all.find((x) => !x.result) ?? all[0]
  const row = (x: CrossReview) => ({ key: x.id, href: toPath(`work/reviews/${x.id}`), on: x.id === r?.id, title: `${TARGET_LABEL[x.target]} · ${x.title}`, meta: <>{x.id} · {x.ref} <State v={x.result ?? waitLabel(x)} /></> })
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
          <ul>{r.checks.map((c) => <li key={c.label}><State v={c.result} tone={c.result === '확인 필요' ? 'amber' : c.result === '통과' ? 'green' : undefined} /> <b>{c.label}</b> <span className="muted"><Linked s={s} text={c.detail} /></span></li>)}</ul>
        </Step>
        {r.target === 'merge_pr' && (() => {
          const pr = s.prs.find((p) => p.id === r.ref)
          return pr ? (
            <Step name="변경" on title={`${pr.gate.files}파일 · ${pr.gate.lines}줄 · ${pr.gate.paths}`}>
              <Changes s={s} pr={pr} />
            </Step>
          ) : null
        })()}
        <Step name="판단" on={!!r.result} title={r.result ? `${r.result} · ${r.decidedAt}` : <State v={waitLabel(r)} live />}>
          {r.result ? r.resultWhy && <p className="muted">{r.resultWhy}</p> : <Waiting s={s} d={d} r={r} />}
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
