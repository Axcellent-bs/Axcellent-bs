// 개발 지표: 자동 처리 범위·AI 승인 감사·에이전트 활동
import { type Dispatch } from 'react'
import { Button, Card, useToast, type Column } from '@dejafix/common-frontend'
import { DEPT_NAME, IRREVERSIBLE, TARGET_LABEL, TODAY, GUARD, OBSERVE_MIN, OBSERVE_N, activeRules, ruleText, type Action, type Activity as Act, type AuditItem, type AutoRule, type Dept, type State as S } from '@/lib/planningData'
import { ActivityList, Dep, Lead, LinkTable, Point, State, Stats, Tag } from '@/components/planning-common/parts'
import { Linked, Ref, hrefOf, prSlug, toPath } from '@/components/planning-common/links'
import { Flow, Head, Layout, ListPane, Step } from '@/components/planning-common/flow'

interface P { s: S; d: Dispatch<Action> }

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
            rowHref={(h) => toPath(`work/prs/${prSlug(h.pr)}`)}
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
        { label: '교차검증 승인', value: n('교차검증 승인'), hint: `거절 ${n('교차검증 거절')}`, href: toPath('work/reviews') },
        { label: '정식이 된 기준', value: n('기준 정식'), hint: `제안 ${n('기준 제안')} · 거부 ${n('학습 거부')}`, href: toPath('metrics/autonomy') },
      ]} />
      <Card as="section" title="건별 기록" sub={`오늘 ${today.length}건 · 최근 순`}>
        {today.length
          ? <LinkTable columns={cols} rows={today.slice().reverse()} rowKey={(x) => `${x.t}|${x.kind}|${x.ref}`} rowHref={(x) => hrefOf(s, x.ref)} rowTitle="이 건 보기" />
          : <div className="empty"><b>오늘 AI가 스스로 처리한 건이 없습니다</b></div>}
      </Card>
    </>
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
    { key: 'all', href: toPath('metrics/activity'), on: peer === 'all', title: '전체', meta: <>{s.activity.length}건</> },
    ...peers.map((x) => ({ key: x, href: toPath(`metrics/activity/${x}`), on: peer === x, title: DEPT_NAME[x], meta: <>{of(x).length}건</> })),
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
