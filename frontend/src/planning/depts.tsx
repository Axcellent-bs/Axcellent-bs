// 다른 부서 시뮬레이터. 실제 부서 에이전트 대신 버튼으로 기획개발에 데이터를 보내고,
// 기획개발이 보낸 메시지·데이터(outbox)를 부서별 창구에서 확인한다.
import { useEffect, useState, type Dispatch } from 'react'
import { Button, Card, Chip, Notice, PageHeader, Select, useToast } from '@dejafix/common-frontend'
import { DEPT_ANSWER, DEPT_NAME, type Action, type Dept, type InboundKind, type State } from './data'
import { Point } from './parts'

interface Sender { kind: InboundKind; point: string; label: string; what: string }
const DEPTS: { dept: Dept; owner: string; senders: Sender[] }[] = [
  { dept: 'support', owner: '병환', senders: [
    { kind: 'support_ticket', point: 'create_ticket', label: '변경 요청 보내기', what: '반복 문의·버그를 기획개발 요청함에 등록' },
    { kind: 'support_simple', point: 'create_ticket', label: '간단 요청 보내기', what: '문구·표시만 고치는 요청 → 기준 안이면 머지까지 자동, 밖이면 교차검증' },
  ] },
  { dept: 'marketing', owner: '재헌', senders: [
    { kind: 'marketing_ticket', point: 'create_ticket', label: '변경 요청 보내기', what: '판매 사이트 변경 요청 (같은 요청이면 연결)' },
    { kind: 'marketing_simple', point: 'create_ticket', label: '간단 요청 보내기', what: '문구·표시만 고치는 요청 → 기준 안이면 머지까지 자동, 밖이면 교차검증' },
  ] },
  { dept: 'hr', owner: '준하', senders: [{ kind: 'hr_draft', point: 'ticket_draft_propose', label: '개선 초안 보내기', what: '③ 반복 패턴으로 만든 개선 초안' }] },
  { dept: 'data', owner: '재현', senders: [
    { kind: 'data_query', point: 'find_related_prs', label: '직전 배포 질의', what: '이상 구간 직전 배포·PR이 있었나' },
    { kind: 'data_draft', point: '개선 초안 (루프 ③)', label: '개선 초안 보내기', what: '반복 지표 이상 → 개선 요청 초안' },
  ] },
  { dept: 'infra', owner: '예진', senders: [{ kind: 'infra_query', point: 'find_related_prs', label: '직전 PR 질의', what: '장애 시각 직전 머지 PR 후보 요청' }] },
  { dept: 'orchestrator', owner: '준하', senders: [{ kind: 'orch_incident', point: 'incident.opened → link_incident', label: '장애 사건 개시', what: '사건을 열고 관련 PR·문단 연결 요청' }] },
  { dept: 'finance', owner: '태윤', senders: [{ kind: 'finance_irreversible', point: 'create_ticket', label: '되돌릴 수 없는 요청 보내기', what: '결제 데이터 삭제 요청 → 사람 확인 대기열' }] },
  { dept: 'safety', owner: '태윤', senders: [{ kind: 'safety_verify', point: 'verify_rollback_target', label: '롤백 교차검증 요청', what: 'infra rollback_deployment(B)의 검증' }] },
]

// 이 화면이 열려 있는 동안 다른 부서 에이전트 몫을 한다: 기획개발이 보낸 질문에 요청 부서가 답하고,
// 교차검증 요청은 검증 부서가 판단해 돌려준다. 기획개발은 남의 답·판단을 스스로 만들지 않는다
const AGENT_DELAY_MS = 1500
function useDeptAgents(s: State, d: Dispatch<Action>) {
  useEffect(() => {
    // 기획서 질문은 출처와 상관없이 요청 부서 에이전트가 답한다 (시드 요청 포함)
    const ask = s.specs.find((sp) => sp.status !== '확정' && sp.question && s.requests.some((r) => r.id === sp.request))
    const review = s.reviews.find((r) => r.lane === 'ai' && !r.result)
    const widen = s.autoPolicy.rules.find((r) => r.status === '승인 대기' && r.approver === '재무 AI')
    if (!ask && !review && !widen) return
    const timer = setTimeout(() => {
      if (ask) {
        const from = s.requests.find((r) => r.id === ask.request)?.from ?? 'support'
        d({ type: 'answerQuestion', spec: ask.id, text: DEPT_ANSWER[from] ?? '요청한 근거 범위 안에서 진행해 주세요.', agent: true })
      } else if (review) d({ type: 'decideReview', review: review.id })
      else if (widen) d({ type: 'decideRule', rule: widen.id, ok: widen.backtest.conflicts.length === 0 })
    }, AGENT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [s, d])
}

export function DeptSimulator({ s, d }: { s: State; d: Dispatch<Action> }) {
  const toast = useToast()
  useDeptAgents(s, d)
  return (
    <main className="page page-wide">
      <PageHeader title="다른 부서 (시뮬레이터)" sub="실제 부서 에이전트 대신 버튼으로 기획개발에 데이터를 보낸다. 오른쪽 창구에는 기획개발이 보낸 메시지·데이터가 쌓인다." />
      <Notice icon="bell">버튼을 누르면 기획개발 화면(5173)이 바로 바뀝니다. 요청은 사이드바 "요청", 메시지는 "소통" 배지에 새로 받은 건수가 뜹니다.</Notice>
      <div className="dept-grid">
        {DEPTS.map(({ dept, owner, senders }) => {
          const inbox = s.outbox.filter((m) => m.to === dept).slice().reverse()
          const unread = inbox.filter((m) => !m.read).length
          return (
            <Card key={dept} as="section" title={DEPT_NAME[dept]} sub={`담당 ${owner}`}>
              <div className="dept-send">
                {senders.map((x) => (
                  <div key={x.kind} className="dept-send-row">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Point>{x.point}</Point>
                      <div className="muted small">{x.what}</div>
                    </div>
                    <Button size="small" variant="primary" onClick={() => { d({ type: 'inbound', kind: x.kind }); toast.show(`${DEPT_NAME[dept]} → 기획개발 ${x.point} 보냄`) }}>{x.label}</Button>
                  </div>
                ))}
                {dept === 'safety' && s.autoPolicy.rules.filter((r) => r.status === '승인 대기' && r.approver === '사람 (태윤)').map((r) => (
                  <div key={r.id} className="dept-send-row">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Point>threshold_change</Point>
                      <div className="muted small">{r.id} {r.path} {r.lines}줄·{r.files}파일 · 근거 {r.basis.join(', ')}</div>
                    </div>
                    <Button size="small" variant="primary" onClick={() => d({ type: 'decideRule', rule: r.id, ok: true })}>태윤 승인</Button>
                    <Button size="small" onClick={() => d({ type: 'decideRule', rule: r.id, ok: false })}>거절</Button>
                  </div>
                ))}
                {s.threads.some((t) => t.peer === dept) && (
                  <div className="dept-send-row">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Point>message</Point>
                      <div className="muted small">기획개발 소통 채널에 메시지</div>
                    </div>
                    <Button size="small" onClick={() => { d({ type: 'deptChat', from: dept }); toast.show(`${DEPT_NAME[dept]} → 기획개발 소통 채널`) }}>메시지 보내기</Button>
                  </div>
                )}
              </div>
              <div className="dept-inbox">
                <div className="dept-inbox-h">
                  기획개발에서 받은 것 {unread > 0 && <Chip tone="amber">새로 {unread}</Chip>}
                  <span style={{ flex: 1 }} />
                  {unread > 0 && <button type="button" className="linkish" onClick={() => d({ type: 'markRead', to: dept })}>모두 읽음</button>}
                </div>
                {inbox.length ? (
                  <ul className="inbox-list">
                    {inbox.slice(0, 5).map((m) => (
                      <li key={m.id} className={m.read ? '' : 'unread'}>
                        <div><Point>{m.point}</Point> <span className="muted small mono">{m.t}</span></div>
                        <div className="small">{m.text}</div>
                      </li>
                    ))}
                  </ul>
                ) : <div className="muted small">아직 없음</div>}
              </div>
            </Card>
          )
        })}
      </div>
    </main>
  )
}

// 기획개발 → 다른 부서 보내기 창구 (연동 탭에 붙는다)
const KINDS = [
  { value: 'get_request_status', label: '처리 상태 회신' },
  { value: 'deploy_notice', label: '개발 완료·배포 알림' },
  { value: 'record_case', label: '결정 기록 (인사)' },
  { value: 'message', label: '자유 메시지' },
]
const TARGETS: Dept[] = ['support', 'marketing', 'hr', 'data', 'infra', 'orchestrator', 'safety']

export function SendWindow({ d }: { d: Dispatch<Action> }) {
  const toast = useToast()
  const [to, setTo] = useState<Dept>('support')
  const [kind, setKind] = useState('deploy_notice')
  const [text, setText] = useState('REQ-12 결제 실패 후 장바구니 유지 개발 완료, dev에 머지됨')
  return (
    <Card title="다른 부서로 보내기" sub="개발 완료·처리 결과·메시지를 보내면 다른 부서 페이지 창구에 쌓인다" as="section">
      <form
        className="send-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (!text.trim()) return
          d({ type: 'send', to, point: kind, text: text.trim() })
          toast.show(`${DEPT_NAME[to]}에 보냄`)
          setText('')
        }}
      >
        <label className="field-inline">받는 부서
          <Select value={to} onChange={(e) => setTo(e.target.value as Dept)} options={TARGETS.map((x) => ({ value: x, label: DEPT_NAME[x] }))} />
        </label>
        <label className="field-inline">종류
          <Select value={kind} onChange={(e) => setKind(e.target.value)} options={KINDS} />
        </label>
        <label className="field-inline" style={{ flex: 1, minWidth: 260 }}>내용
          <input className="text-input" value={text} onChange={(e) => setText(e.target.value)} placeholder="보낼 내용" />
        </label>
        <Button type="submit" variant="primary" disabled={!text.trim()}>보내기</Button>
      </form>
    </Card>
  )
}
