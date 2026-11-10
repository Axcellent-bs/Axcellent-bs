// 개발 작업·PR과 AI 교차검증. 프로젝트 탭과 같은 틀(왼쪽 목록 / 가운데 세로 흐름 / 오른쪽 곁 블록)
import { useEffect, useState, type Dispatch, type ReactNode } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { CROSS_CHECK_TIMEOUT_MIN, EVIDENCE_SOURCE, TARGET_LABEL, OBSERVE_MIN, type Action, type CrossEvidence, type CrossReview, type PR, type State as S } from '@/lib/planningData'
import { codeOf, traceOf, type CodeLine } from '@/components/dev/trace'
import { DiffFiles } from '@/components/dev/diff'
import { DEV_API, repoApi, type RepoFile } from '@/lib/planning'
import { State, Tag } from '@/components/planning-common/parts'
import { Linked, Ref, convoHref, hrefOf, prFromSlug, prName, prSlug, repoPrHref, reviewOf, toPath, idText } from '@/components/planning-common/links'
import { Flow, Head, Layout, ListPane, Pairs, Side, Step, mergedLabel } from '@/components/planning-common/flow'
import { RepoTargetBlock } from '@/components/planning-common/repo'
import { CodingLabel, CodingStep, OriginTag, RelatedTags, codingState, isRunning } from '@/components/dev/coding'
import { humanizePlanIds } from '@/lib/format'
import './hier.css'

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
export function exampleFiles(s: S, pr: PR): RepoFile[] {
  const by = new Map<string, CodeLine[]>()
  for (const c of codeOf(s, pr)) by.set(c.path, [...(by.get(c.path) ?? []), c])
  // 줄 기록(예시)은 diff로, 파일 단위 기록(실제 코딩 기록)은 줄 없이 파일만 보여 준다
  return [...by].map(([path, ls]) => ({
    path, status: 'modified', additions: ls.length, deletions: 0, previous_path: null, truncated: false,
    patch: ls[0].line == null ? null : [`@@ -${ls[0].line},0 +${ls[0].line},${ls.length} @@`, ...ls.map((l) => `+${l.text}`)].join('\n'),
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
  const repoLink = n != null ? <a href={repoPrHref(n, conn, 'files')} className="plain-link small">{prName(pr)} 변경 파일</a> : null
  if (err) return <p className="muted small">레포 diff를 읽지 못했어요 {repoLink}</p>
  if (!files) return <p className="muted small">읽는 중</p>
  return (
    <>
      {repoLink}
      <DiffFiles files={files} draftKey={n != null && conn ? `${conn}#${n}` : pr.id} editable={!pr.merged} compact />
    </>
  )
}

/** 교차검증 근거 줄: 어느 부서에 물어 무슨 답(또는 못 모음)이 왔는지 */
function EvidenceList({ lines }: { lines: CrossEvidence[] }) {
  return (
    <ul aria-label="교차검증 근거">
      {lines.map((e) => (
        <li key={e.source + e.label}>
          <State v={e.ok ? '통과' : '확인 필요'} tone={e.ok ? 'green' : 'amber'} /> <b>{e.label}</b> <span className="muted">{EVIDENCE_SOURCE[e.source] ?? e.source} · {e.value}</span>
        </li>
      ))}
    </ul>
  )
}

// 판단 전: 무엇을 기다리는지 · 얼마나 · 언제 사람에게 넘어가는지 · 지금 사람이 할 수 있는 것
function Waiting({ s, d, r, listed }: P & { r: CrossReview; listed?: boolean }) {
  const toast = useToast()
  const now = useNow()
  const waited = waitedMin(r, now)
  const left = waited === undefined ? undefined : CROSS_CHECK_TIMEOUT_MIN - waited
  const open = r.checks.filter((c) => c.result !== '통과').map((c) => c.label)
  const prHref = r.target === 'merge_pr' ? toPath(`work/prs/${prSlug(r.ref)}`) : hrefOf(s, r.ref)
  return (
    <>
      <Pairs items={[
        ['기다리는 판정', r.lane === 'ai' ? `${r.verifier} 판정` : r.irreversible ? `사람 확인 · ${r.irreversible}` : '사람 확인'],
        ['대기', waited === undefined ? r.t : `${r.t}부터 · ${waited}분`],
        ...(r.lane === 'ai' ? [['사람에게 넘어감', left === undefined ? `${CROSS_CHECK_TIMEOUT_MIN}분 뒤 자동` : left > 0 ? `${left}분 뒤 자동` : '곧 넘어감'] as [string, string]] : []),
        ...(listed ? [] : open.length ? [['확인할 항목', open.join(' · ')] as [string, string]] : r.routeWhy.length ? [['확인할 항목', r.routeWhy.join(' · ')] as [string, string]] : []),
      ]} />
      {!!r.evidence?.length && <EvidenceList lines={r.evidence} />}
      <div className="actions">
        <Button variant="primary" onClick={() => { d({ type: 'humanDecide', review: r.id, ok: true }); toast.show(`${idText(r.ref)} 진행`) }}>확인하고 진행</Button>
        {r.lane === 'human' && <Button onClick={() => { d({ type: 'humanDecide', review: r.id, ok: false }); toast.show(`${idText(r.ref)} 보류`) }}>보류</Button>}
        {prHref && <a href={prHref} className="btn-quiet px-3 py-1">{r.target === 'merge_pr' ? 'PR에서 수정 요청' : '기획서 보기'}</a>}
      </div>
    </>
  )
}
export function Work({ s, d, item, repo }: P & { item?: string; repo?: string }) {
  const [line, setLine] = useState<CodeLine>()
  useEffect(() => { setLine(undefined) }, [item])
  const prOf = (taskId: string) => s.prs.find((p) => p.task === taskId)
  // 주소가 작업 id면 그 작업의 PR, PR이 아직 없으면 작업만 보여 준다 (직접 개발 직후)
  const byTask = s.tasks.find((t) => t.id === item)
  const taskOnly = byTask && !prOf(byTask.id) ? byTask : undefined
  const sel = (byTask && prOf(byTask.id)) ?? s.prs.find((p) => p.id === prFromSlug(item)) ?? s.prs.find((p) => p.target?.status === 'proposed') ?? s.prs.find((p) => reviewOf(s, p.id)) ?? s.prs[s.prs.length - 1]
  const groupOf = (taskId: string, state: string) => { const p = prOf(taskId); return state === '완료' || p?.merged ? 2 : p && reviewOf(s, p.id) ? 1 : 0 }
  // 레포를 고르면 그 레포로 올리는(올린) 작업만. 아직 레포가 안 정해진 작업은 전체에서만 보인다
  const repoOf = (t: S['tasks'][number]) => prOf(t.id)?.target?.connectionId ?? t.coding?.connection ?? null
  const tasks = s.tasks.slice().reverse().filter((t) => !repo || repoOf(t) === repo)
  const row = (t: S['tasks'][number]) => {
    const p = prOf(t.id)
    const xr = p && reviewOf(s, p.id)
    const coding = t.coding && !p?.merged && !p?.target && !xr ? codingState(t.coding) : undefined
    return {
      key: t.id,
      href: p ? toPath(`work/prs/${prSlug(p.id)}`) : t.coding ? toPath(`work/prs/${encodeURIComponent(t.id)}`) : toPath(`work/specs/${t.spec}/${t.para}`),
      on: taskOnly ? t.id === taskOnly.id : !!p && p.id === sel?.id,
      title: t.title,
      meta: <><span className="mono idc" title={t.id}>{idText(t.id)}</span>{p ? <> <span className="mono idc">{prName(p)}</span></> : ''}{p?.target && p.vcsNumber == null ? ` · ${p.target.repo.split('/').pop()}` : ''} <State v={p?.target?.status === 'proposed' ? '레포 승인 대기' : xr ? waitLabel(xr) : coding ?? t.state} live={!!coding && !!t.coding && isRunning(t.coding.status)} /> <OriginTag s={s} t={t} /></>,
    }
  }
  const list = <ListPane empty="작업 없음" groups={(['진행 중', '교차검증·사람 확인', '완료'] as const).map((label, i) => ({ label, rows: tasks.filter((t) => groupOf(t.id, t.state) === i).map(row) }))} />
  if (taskOnly) {
    const text = s.specs.find((x) => x.id === taskOnly.spec)?.paras.find((p) => p.id === taskOnly.para)?.text
    return (
      <Layout list={null} side={<Side title="작업 목록"><div className="dev-side-list">{list}</div></Side>} head={<Head kicker={<><span className="mono idc" title={taskOnly.id}>{idText(taskOnly.id)}</span> <span className="mono idc" title={taskOnly.spec}>{idText(taskOnly.spec)}</span> <OriginTag s={s} t={taskOnly} /><RelatedTags s={s} t={taskOnly} /></>} title={taskOnly.title} side={taskOnly.coding ? <CodingLabel c={taskOnly.coding} /> : <State v={taskOnly.state} />} />}>
        <Flow>
          <Step name="작업" on href={toPath(`work/specs/${taskOnly.spec}/${taskOnly.para}`)} title={<><span className="mono idc" title={taskOnly.id}>{idText(taskOnly.id)}</span> <span className="mono idc" title={taskOnly.spec}>{idText(taskOnly.spec)} {taskOnly.para}</span></>}>
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
      list={null}
      head={<Head kicker={<>{sel.vcsNumber != null ? <a href={repoPrHref(sel.vcsNumber, sel.target?.connectionId)} className="ref mono idc">{prName(sel)}</a> : <span className="mono idc" title={sel.id}>{idText(sel.id)}</span>} {task && <span className="mono idc" title={task.id}>{idText(task.id)}</span>} <span className="mono">{sel.branch}</span>{task && <> · <OriginTag s={s} t={task} /></>}<RelatedTags s={s} t={task} /></>} title={sel.title.replace(/^[A-Z]\d+: /, '')} side={<State v={sel.merged ? '완료' : xr ? waitLabel(xr) : last === '불일치' || rejected ? '불일치' : '머지 대기'} />} />}
      side={<>
        <Side title="출처" sum={`${chain.length}단계`}>
          {chain.map((x, i) => <p key={i} className="pside-msg"><b>{x.kind}</b> {x.href ? <a href={x.kind === '문단' && spec && para ? toPath(`work/specs/${spec.id}/${para.id}`) : toPath(x.href)} className="plain-link"><Chipped text={x.title} /></a> : <Chipped text={x.title} />}</p>)}
        </Side>
        <Side title="작업 목록"><div className="dev-side-list">{list}</div></Side>
      </>}
    >
      <Flow>
        <Step name="작업" on href={task ? toPath(`work/specs/${task.spec}/${task.para}`) : undefined} title={task ? <><span className="mono idc" title={task.id}>{idText(task.id)}</span> <span className="mono idc" title={task.spec}>{idText(task.spec)} {task.para}</span></> : '-'}>
          {para && <p>{humanizePlanIds(para.text)}</p>}
          {para?.warn && <Tag tone="amber">경고 {para.warn.card}</Tag>}
          {para?.incident && <Tag tone="amber">장애 {para.incident}</Tag>}
        </Step>
        {task && <CodingStep task={task} />}
        {sel.target && (
          <Step name="대상 레포" on={sel.target.status === 'approved'} title={sel.target.status === 'approved' ? sel.target.repo : sel.target.status === 'rejected' ? '거절됨' : '승인 대기'}>
            <RepoTargetBlock key={sel.id} p={sel} d={d} />
          </Step>
        )}
        <Step name="범위 점검" on title={<>{sel.gate.result} · {sel.gate.lines}줄·{sel.gate.files}파일 · <span className="mono">{sel.gate.paths}</span></>} />
        <Step name="리뷰" on={last === '통과'} title={sel.reviews.join(' → ')}>
          {!sel.merged && (last === '불일치' || rejected) && <Button size="small" onClick={() => d({ type: 'reviewPr', pr: sel.id })}>다시 리뷰</Button>}
        </Step>
        {!!sel.crossEvidence?.length && (() => {
          const ev = sel.crossEvidence
          const miss = ev.filter((e) => !e.ok).length
          return (
            <Step name="교차검증" on={miss === 0} title={`${[...new Set(ev.map((e) => EVIDENCE_SOURCE[e.source] ?? e.source))].join('·')} 근거 ${ev.length}건 · ${miss ? `확인 필요 ${miss}` : '모두 통과'}`}>
              <EvidenceList lines={ev} />
            </Step>
          )
        })()}
        <Step name="머지" on={sel.merged} href={sel.url || (xr ? toPath(`work/reviews/${xr.id}`) : undefined)} title={sel.merged ? mergedLabel(sel) : xr ? `${waitLabel(xr)} · ${idText(xr.id)}` : rejected ? '검증자 거절' : '머지 기다림'}>
          {xr && <p className="muted">{xr.verifier}</p>}
        </Step>
      </Flow>
      <Trace s={s} pr={sel} line={line} setLine={setLine} />
    </Layout>
  )
}

/** 글 안의 코드 번호(T-·SPEC-·REQ-·PR #)만 칩으로. 링크 안에서도 쓸 수 있게 링크는 만들지 않는다 */
const CODE_RE = /(REQ-\d+|SPEC-[A-Z]?\d+(?: R\d+)?|T-[A-Z]?\d+|PR #\d+|PROP-\d+|DOC-\d+|DEP-\d+|XR-\d+|AR-\d+)/
/** 줄바꿈(\n)과 " · "로 이어 붙인 항목(문제: … · 목표: …)을 줄로 나눠 보여 준다 */
export const Lines = ({ text }: { text: string }) => <>{text.split(/\n|\s+·\s+/).filter(Boolean).map((x, i) => <span key={i} className="dev-line"><Chipped text={x} /></span>)}</>
const Chipped = ({ text }: { text: string }) => <>{text.split(CODE_RE).map((x, i) => (i % 2 ? <span key={i} className="mono idc" title={x}>{idText(x)}</span> : x))}</>

// 코드 역추적 (코드 줄 → PR → 작업 → 문단 → 결정 → 요청 → 근거). 연결은 문단 id로만 따라간다
function Trace({ s, pr, line: picked, setLine }: { s: S; pr: PR; line?: CodeLine; setLine: (x?: CodeLine) => void }) {
  const lines = codeOf(s, pr)
  const line = picked ?? lines[0]
  const steps = traceOf(s, pr.id, line)
  return (
    <section id="trace" className="flow-sec">
      <h2>코드 출처</h2>
      <div className="trace">
        <div className="trace-code">
          <div className="trace-h">{lines.some((l) => l.line != null) ? '바뀐 줄' : '바뀐 파일'}</div>
          {!lines.length && <p className="muted small">바뀐 곳 기록 없음 (PR 화면의 변경 파일에서 봐요)</p>}
          {lines.map((l) => (
            <button key={`${l.path}:${l.line ?? ''}`} type="button" className={`code-line${line && l.line === line.line && l.path === line.path ? ' on' : ''}`} onClick={() => setLine(l)}>
              <span className="muted small mono">{l.line != null ? `${l.path}:${l.line}` : l.path}</span>
              <code>{l.text}</code>
            </button>
          ))}
        </div>
        <ol className="trace-chain">
          {steps.map((x, i) => (
            <li key={i} className="trace-step">
              <span className="trace-kind">{x.kind}</span>
              <div>
                <div>{x.href ? <a href={toPath(x.href)}><Chipped text={x.title} /></a> : <b><Chipped text={x.title} /></b>}</div>
                {x.detail && <div className="muted small"><Lines text={x.detail} /></div>}
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
  const row = (x: CrossReview) => ({ key: x.id, href: toPath(`work/reviews/${x.id}`), on: x.id === r?.id, title: `${TARGET_LABEL[x.target]} · ${x.title}`, meta: <><span className="mono idc" title={x.id}>{idText(x.id)}</span> <span className="mono idc" title={x.ref}>{idText(x.ref)}</span> <State v={x.result ?? waitLabel(x)} /></> })
  const list = <ListPane empty="대기 없음" groups={[
    { label: '교차검증 대기', rows: all.filter((x) => !x.result && x.lane === 'ai').map(row) },
    { label: '사람 확인 대기', rows: all.filter((x) => !x.result && x.lane === 'human').map(row) },
    { label: '처리됨', rows: all.filter((x) => x.result).map(row) },
  ]} />
  if (!r) return <Layout list={list}><div className="empty"><b>대기 없음</b></div></Layout>
  const cnt = (k: CrossReview['checks'][number]['result']) => r.checks.filter((c) => c.result === k).length
  const look = r.checks.filter((c) => c.result !== '통과')
  const passed = r.checks.filter((c) => c.result === '통과')
  const check = (c: CrossReview['checks'][number]) => <li key={c.label}><State v={c.result} tone={c.result === '확인 필요' ? 'amber' : c.result === '통과' ? 'green' : undefined} /> <b>{c.label}</b> <span className="muted"><Linked s={s} text={c.detail} /></span></li>
  return (
    <Layout
      list={list}
      head={<Head kicker={<><span className="mono idc" title={r.id}>{idText(r.id)}</span> {TARGET_LABEL[r.target]} · <Ref s={s} id={r.ref} /> · {r.t}</>} title={r.title} side={<State v={r.result ?? waitLabel(r)} />} />}
      side={
        <>
          <Side title="판단하는 쪽">
            <Pairs items={[
              ['검증자', r.verifier],
              ['만든 쪽이 승인', r.lane === 'ai' ? '아님 · 다른 부서 AI' : '해당 없음'],
              ...(r.irreversible ? [['되돌릴 수 없음', r.irreversible] as [string, string]] : []),
            ]} />
          </Side>
          <Side title="대화" href={convoHref(r.ctx)} sum={<span className="mono idc" title={r.ctx}>{idText(r.ctx)}</span>} />
        </>
      }
    >
      {!r.result && (
        <section className="dev-decide" aria-label="판단">
          <div className="dev-decide-h">
            <b>{look.length ? `확인할 것 ${look.length}개` : '판단 대기'}</b>
            <span>{r.recommend ? `AI 추천: ${r.recommend}` : 'AI 추천 없음'}</span>
          </div>
          {look.length > 0 && (
            <ul className="look">
              {look.map((c) => <li key={c.label}><b>{c.label}</b><span className="muted"><Linked s={s} text={c.detail} /></span></li>)}
            </ul>
          )}
          <Waiting s={s} d={d} r={r} listed={look.length > 0} />
        </section>
      )}
      <Flow>
        <Step name="검토로 온 이유" on title={r.routeWhy.join(', ') || '-'} />
        <Step name="AI 요약" on title={r.recommend ? `추천 ${r.recommend}` : '추천 없음'}>
          <p><Linked s={s} text={r.summary} /></p>
          {r.recommendWhy && <p className="muted">{r.recommendWhy}</p>}
        </Step>
        <Step name="규칙 점검" on title={`통과 ${cnt('통과')} · 확인 필요 ${cnt('확인 필요')} · 걸림 ${cnt('걸림')}`}>
          <ul className="dev-checks">{look.map(check)}</ul>
          {passed.length > 0 && (
            <details className="dev-pass">
              <summary>통과 {passed.length}개 보기</summary>
              <ul>{passed.map(check)}</ul>
            </details>
          )}
        </Step>
        {r.target === 'merge_pr' && (() => {
          const pr = s.prs.find((p) => p.id === r.ref)
          return pr ? (
            <Step name="변경" on title={<>{pr.gate.files}파일 · {pr.gate.lines}줄 · <span className="mono">{pr.gate.paths}</span></>}>
              <Changes s={s} pr={pr} />
            </Step>
          ) : null
        })()}
        <Step name="결정" on={!!r.result} title={r.result ? `${r.result} · ${r.decidedAt}` : <State v={waitLabel(r)} live />}>
          {r.result ? r.resultWhy && <p className="muted">{r.resultWhy}</p> : <p className="dev-done-note">맨 위 판단 상자에서 정해요</p>}
        </Step>
        {r.target === 'merge_pr' && r.lane === 'ai' && (() => {
          const o = s.autoPolicy.observing.find((x) => x.pr === r.ref)
          const rule = s.autoPolicy.rules.find((x) => x.basis.includes(r.ref))
          return (
            <Step name="머지 뒤 지켜보기" on={o?.result === '정상'} href={rule ? hrefOf(s, rule.id) : undefined} title={!o ? '-' : rule ? `${idText(rule.id)} ${rule.status}` : o.result}>
              {o && (
                <Pairs items={[
                  ['머지', o.mergedAt],
                  ['지켜본 시간', `${OBSERVE_MIN}분 · ${humanizePlanIds(o.result)}`],
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
