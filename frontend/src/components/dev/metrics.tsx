// 개발 지표: 자동 처리 범위·AI 단독 처리(사후 감사)·에이전트 활동
import { useEffect, useState } from 'react'
import { Button, Card, useToast, type Column } from '@dejafix/common-frontend'
import { IRREVERSIBLE, TODAY, type Activity as Act, type AuditItem, type Dept, type State as S } from '@/lib/planningData'
import { DEV_API, autoPolicyApi, type AutoPolicyInfo, type SendAction } from '@/lib/planning'
import { activityDir, activityPeers, deptLabel, type Part } from '@/lib/devView'
import { useSession } from '@/demo/session'
import { PreparingChip, PreparingNotice } from '@/components/Preparing'
import { ActivityList, Lead, LinkTable, Point, State, Stats, Tag } from '@/components/planning-common/parts'
import { Linked, Ref, hrefOf, prSlug, toPath, idText } from '@/components/planning-common/links'
import { Flow, Head, Layout, ListPane, Step } from '@/components/planning-common/flow'
import { humanizePlanIds } from '@/lib/format'

interface P { s: S; d: SendAction }

// ---------------------------------------------------------------- 자동 처리 범위 (백엔드 AutoPolicy: 단계·허용 경로·연속 정상·승급 제안·이력·변경 기록)
// 넓히기(승급)는 연속 정상 건수가 차면 서버가 제안하고 운영자가 승인한다. 줄이기는 자동 머지가 장애에 연결되면 서버가 바로 한다.
// 경로별 학습 기준(머지 뒤 관찰 → 기준 제안)은 백엔드에 아직 없어 준비 중으로 둔다.
export function Autonomy({ s, d }: P & { item?: string }) {
  const toast = useToast()
  const { account } = useSession()
  const pol = s.autoPolicy
  const [info, setInfo] = useState<AutoPolicyInfo | null>(null)
  useEffect(() => {
    let alive = true
    autoPolicyApi(DEV_API).get().then((x) => alive && setInfo(x)).catch(() => alive && setInfo(null))
    return () => { alive = false }
  }, [pol.level, pol.proposal?.to])
  const [busy, setBusy] = useState(false)
  const linked = pol.history.filter((h) => h.result === '장애 연결').length
  const cur = info?.levels.find((l) => l.level === pol.level)
  const next = pol.proposal ? info?.levels.find((l) => l.level === pol.proposal?.to) : undefined
  const decide = async (ok: boolean) => {
    setBusy(true)
    const done = await d({ type: ok ? 'approvePromotion' : 'rejectPromotion' })
    setBusy(false)
    if (done) toast.show(ok ? `L${pol.proposal?.to} 승급 승인` : '승급 보류 · 연속 건수 다시 셈')
  }
  return (
    <>
      <Lead>
        자동 머지는 지금 단계의 줄·파일 한도와 허용 경로 안에서만 해요. 연속 정상{info ? ` ${info.promote_after}건` : ''}이면 서버가 한 단계 승급을 제안하고 운영자가 승인해요.
        자동 머지가 장애에 연결되면 한 단계 바로 줄어요.
      </Lead>
      <Stats items={[
        { label: '지금 단계', value: pol.level == null ? '-' : `L${pol.level}`, hint: cur?.name },
        { label: '자동 머지 한도', value: cur ? `${cur.lines}줄·${cur.files}파일` : '-', hint: pol.paths ? `허용 경로 ${pol.paths}` : undefined },
        { label: '연속 정상', value: `${pol.streak ?? 0}${info ? ` / ${info.promote_after}` : ''}`, hint: '다 차면 승급 제안' },
        { label: '승급 제안', value: pol.proposal ? `L${pol.proposal.to}` : '없음', tone: pol.proposal ? 'alert' : 'quiet' },
        { label: '자동 머지 / 그중 장애', value: `${pol.history.length} / ${linked}`, hint: '건' },
      ]} />
      {pol.proposal && (
        <Card as="section" title="승급 제안" sub={`L${pol.level ?? '-'} → L${pol.proposal.to}${next ? ` (${next.name} · ${next.lines}줄·${next.files}파일)` : ''}`}>
          <p className="small">{pol.proposal.why} <span className="muted mono">{pol.proposal.t}</span></p>
          {account?.admin ? (
            <div className="actions">
              <Button variant="primary" disabled={busy} onClick={() => void decide(true)}>승급 승인</Button>
              <Button disabled={busy} onClick={() => void decide(false)}>보류</Button>
            </div>
          ) : <p className="muted small">운영자(관리자)가 승인해요.</p>}
        </Card>
      )}
      <div className="auto-grid">
        <Card as="section" title="자동 처리 단계" sub="개발 서버 기준표">
          {info ? (
            <ul className="levels">
              {info.levels.map((l) => <li key={l.level} className={l.level === pol.level ? 'on' : undefined}><b>L{l.level} {l.name}</b> <span className="muted small">{l.lines}줄·{l.files}파일 이하</span></li>)}
            </ul>
          ) : <p className="muted small">단계표를 읽지 못했어요 (개발·배포 서버 꺼짐)</p>}
        </Card>
        <Card as="section" title="되돌릴 수 없는 일" sub="항상 사람이 확인">
          <ul className="levels">
            {IRREVERSIBLE.map((x) => <li key={x.id}><b>{x.label}</b> <span className="muted small">{x.words.join(', ')}</span></li>)}
          </ul>
        </Card>
      </div>
      <Card as="section" title={<>경로별 학습 기준 <PreparingChip /></>} sub="머지 뒤 지켜보기 → 같은 경로 기준 제안">
        <PreparingNotice what="학습된 자동 머지 기준" source="개발 서버의 관찰 기반 학습" />
      </Card>
      <Card as="section" title="변경 기록" sub="단계를 누가 언제 바꿨나">
        {pol.log.length ? (
          <ul className="levels">
            {pol.log.slice().reverse().map((x, i) => (
              <li key={i}><Tag tone={x.by === '자동' ? 'amber' : undefined}>{x.by}</Tag> <span className="muted small mono">{x.t}</span> <Linked s={s} text={x.text} /></li>
            ))}
          </ul>
        ) : <div className="muted small">아직 바뀐 적이 없어요.</div>}
      </Card>
      <Card as="section" title="자동 머지한 PR" sub="머지 뒤 결과까지">
        {pol.history.length ? (
          <LinkTable
            rows={pol.history.slice().reverse()}
            rowKey={(h) => h.pr}
            rowHref={(h) => toPath(`work/prs/${prSlug(h.pr)}`)}
            rowTitle="PR과 코드 출처 보기"
            columns={[
              { key: 't', header: '시각', render: (h) => <span className="mono small">{h.t}</span> },
              { key: 'pr', header: 'PR', render: (h) => <span className="mono idc" title={h.pr}>{idText(h.pr)}</span> },
              { key: 'req', header: '요청', render: (h) => <Ref s={s} id={h.req} /> },
              { key: 'size', header: '변경', render: (h) => `${h.lines}줄·${h.files}파일` },
              { key: 'result', header: '결과', render: (h) => <State v={h.result === '정상' ? '완료' : '장애 발생'} /> },
            ]}
          />
        ) : <div className="muted small">아직 자동 머지한 PR이 없어요.</div>}
      </Card>
    </>
  )
}

// ---------------------------------------------------------------- AI 단독 처리 (사람 사후 감사)
// 감사 목록 API는 없어서 서버가 남긴 활동(자동 머지·자동 확정)과 자동 범위 변경 기록에서 센다 (lib/devView auditOf)
export function Audit({ s }: P) {
  const today = s.audit.filter((x) => x.t.startsWith(TODAY))
  const n = (k: AuditItem['kind']) => today.filter((x) => x.kind === k).length
  const cols: Column<AuditItem>[] = [
    { key: 't', header: '시각', render: (x) => <span className="mono small">{x.t.slice(6)}</span> },
    { key: 'kind', header: 'AI가 한 일', render: (x) => <State v={x.kind} /> },
    { key: 'ref', header: '대상', render: (x) => <>{x.ref.startsWith('PR') || x.ref.startsWith('SPEC') ? <Ref s={s} id={x.ref} /> : <span className="small">{x.ref}</span>} {x.what && <span className="small">{humanizePlanIds(x.what)}</span>}</> },
    { key: 'basis', header: '근거', render: (x) => <span className="small"><Linked s={s} text={x.basis} /></span> },
    { key: 'verifier', header: '판정한 쪽', render: (x) => <span className="small">{x.verifier}</span> },
    { key: 'result', header: '결과', render: (x) => <span className="small">{humanizePlanIds(x.result)}</span> },
  ]
  return (
    <>
      <Lead>오늘 AI가 사람 확인 없이 처리한 일. 문제가 있으면 자동 머지 기준 화면에서 단계를 확인해요.</Lead>
      <Stats items={[
        { label: '기획서 자동 확정', value: n('자동 확정') },
        { label: '자동 머지', value: n('자동 머지'), hint: '지금 단계 한도 안' },
        { label: '교차검증 근거 뒤 자동 머지', value: n('교차검증 승인'), hint: '근거 모두 통과', href: toPath('work/reviews') },
        { label: '자동 범위 제안 · 축소', value: `${n('기준 제안')} · ${n('기준 해제')}`, href: toPath('metrics/autonomy') },
      ]} />
      <Card as="section" title="건별 기록" sub={`오늘 ${today.length}건 · 최근 순`}>
        {today.length
          ? <LinkTable columns={cols} rows={today.slice().reverse()} rowKey={(x) => `${x.t}|${x.kind}|${x.ref}|${x.basis}`} rowHref={(x) => (x.ref.startsWith('PR') || x.ref.startsWith('SPEC') ? hrefOf(s, x.ref) : toPath('metrics/autonomy'))} rowTitle="이 건 보기" />
          : <div className="empty"><b>오늘 AI가 스스로 처리한 건이 없습니다</b></div>}
      </Card>
    </>
  )
}

// 부서별로 연결 지점을 묶는다: 방향(보냄·받음·내부) → 연결 지점 → 건수. 자기 부서는 화면(part)에 따라
function byPoint(items: Act[], part: Part) {
  const m = new Map<string, Act[]>()
  for (const a of items) {
    const k = `${activityDir(a, part)}|${a.point}`
    m.set(k, [...(m.get(k) ?? []), a])
  }
  return Array.from(m, ([k, v]) => ({ dir: k.split('|')[0], point: k.split('|').slice(1).join('|'), items: v })).sort((a, b) => b.items.length - a.items.length)
}

// ---------------------------------------------------------------- 에이전트 활동 (부서 → 연결 지점 → 건수)
export function Activity({ s, item, part }: { s: S; item?: string; part: Part }) {
  const peers = activityPeers(s.activity, part)
  const peer = (item && peers.includes(item as Dept) ? item : 'all') as Dept | 'all'
  const of = (x: Dept | 'all') => s.activity.filter((a) => x === 'all' || activityPeers([a], part).includes(x))
  const rows = of(peer)
  const list = <ListPane groups={[{ label: '부서', rows: [
    { key: 'all', href: toPath('metrics/activity'), on: peer === 'all', title: '전체', meta: <>{s.activity.length}건</> },
    ...peers.map((x) => ({ key: x, href: toPath(`metrics/activity/${x}`), on: peer === x, title: deptLabel(x), meta: <>{of(x).length}건</> })),
  ] }]} />
  return (
    <Layout
      list={list}
      head={<Head kicker={`활동 ${rows.length}건`} title={peer === 'all' ? '전체 부서' : deptLabel(peer)} />}
      below={
        <details className="proj-activity">
          <summary>활동 로그 {rows.length}</summary>
          <ActivityList s={s} items={rows.slice().reverse()} />
        </details>
      }
    >
      <Flow label="연결 지점">
        {byPoint(rows, part).map((g) => (
          <Step key={`${g.dir}|${g.point}`} name={g.dir} on title={<><Point>{g.point}</Point> · {g.items.length}건</>}>
            {g.items.slice(-2).reverse().map((a, i) => <p key={i}><span className="mono muted">{a.t.slice(6)}</span> {deptLabel(a.from)} → {deptLabel(a.to)} · <Linked s={s} text={a.text} /></p>)}
          </Step>
        ))}
      </Flow>
    </Layout>
  )
}
