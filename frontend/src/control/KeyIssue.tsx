import { useState, type FormEvent } from 'react'
import { issueKey, revokeKey, useCompany, useKeys, type IssuedKey, type KeyStatus } from '@/demo/company'

// 연결 키 발급 (AXcellent 운영팀). 키에는 회사 정보가 없고 연결 권한만 있다. 누구에게 보냈는지는 운영팀 메모로만 남긴다.
// 고객사 시스템 관리자가 키를 넣고 회사 정보를 입력하면, 그 회사 이름이 여기 목록에 나타난다.
const STATUS: Record<KeyStatus, { label: string; tone: string }> = {
  issued: { label: '발급됨 · 연결 전', tone: 'bg-amber-50 text-amber-800' },
  connected: { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700' },
  revoked: { label: '폐기', tone: 'bg-ink-100 text-ink-500' },
}
const mask = (k: string) => `${k.slice(0, 8)}••••-••••`
const day = (iso: string) => new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export default function KeyIssue() {
  const keys = useKeys()
  const current = useCompany()
  const [memo, setMemo] = useState('')
  const [fresh, setFresh] = useState<IssuedKey | null>(null)
  const [copied, setCopied] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setFresh(issueKey(memo.trim()))
    setCopied(false)
    setMemo('')
  }
  const copy = async (k: string) => {
    try {
      await navigator.clipboard.writeText(k)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 pb-12">
      <header>
        <p className="text-sm text-ink-500">관제 센터</p>
        <h1 className="text-2xl font-semibold">연결 키 발급</h1>
        <p className="mt-1 text-sm text-ink-500">키에는 회사 정보가 없어요. 고객사 시스템 관리자가 키를 넣은 뒤 회사 정보를 직접 입력해서 연결해요.</p>
      </header>

      <section className="card p-6">
        <h2 className="card-title">새 키 발급</h2>
        <form onSubmit={submit} className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
          <input className="field" placeholder="메모 (선택) · 예: 베이커리 체인 도입 문의, 담당 김○○ — 키에는 안 들어가요" value={memo} onChange={(e) => setMemo(e.target.value)} aria-label="메모" />
          <button className="btn-primary px-5">발급</button>
        </form>
        {fresh && (
          <div className="mt-4 flex flex-wrap items-center gap-3 border border-dashed border-brand-500 bg-brand-50 px-5 py-4">
            <span className="text-sm text-brand-700">새 연결 키</span>
            <span className="select-all font-mono text-lg tracking-wider">{fresh.key}</span>
            <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={() => copy(fresh.key)}>{copied ? '복사됨' : '복사'}</button>
            <span className="w-full text-xs text-ink-500">전체 키는 지금만 보여요. 고객사 담당자에게 전달하세요. 회사 정보는 고객사가 연결할 때 입력해요.</span>
          </div>
        )}
      </section>

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">키</th>
              <th className="px-3 py-3 font-medium">연결한 고객사</th>
              <th className="px-3 py-3 font-medium">메모</th>
              <th className="px-3 py-3 font-medium">발급</th>
              <th className="px-3 py-3 font-medium">상태</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {keys.map((k) => (
              <tr key={k.key}>
                <td className="px-5 py-3 font-mono text-xs">{mask(k.key)}</td>
                <td className="px-3 py-3">
                  {k.company ? (
                    <>
                      <p className="font-medium">{k.company.name}</p>
                      <p className="text-xs text-ink-500">{k.company.industry}</p>
                    </>
                  ) : <span className="text-ink-500">아직 없음</span>}
                </td>
                <td className="px-3 py-3 text-ink-700">{k.memo ?? '—'}</td>
                <td className="px-3 py-3 text-ink-700">{day(k.issuedAt)}</td>
                <td className="px-3 py-3">
                  <span className={`chip ${STATUS[k.status].tone}`}>{STATUS[k.status].label}</span>
                  {k.status === 'connected' && current && current.id === k.company?.id && <span className="ml-2 text-xs text-ink-500">이 데모에 연결된 회사</span>}
                </td>
                <td className="px-5 py-3 text-right">
                  {k.status !== 'revoked' && (
                    <button type="button" className="btn-quiet px-2.5 py-1 text-xs" onClick={() => revokeKey(k.key)}>폐기</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-ink-500">폐기한 키로는 연결할 수 없어요. 그 키로 이미 연결돼 있으면 연결도 끊겨요.</p>
    </div>
  )
}
