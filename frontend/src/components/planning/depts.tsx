// 기획 → 다른 부서 보내기 창구 (연동 탭에 붙는다)
import { useState, type Dispatch } from 'react'
import { Button, Card, Select, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, type Action, type Dept } from '@/lib/planningData'

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
  const [text, setText] = useState('')
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
