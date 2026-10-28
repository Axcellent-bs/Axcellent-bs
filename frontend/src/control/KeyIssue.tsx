import { useState, type FormEvent } from 'react'
import { issueKey, revokeKey, useCompany, useKeys, type IssuedKey, type KeyStatus } from '@/demo/company'

// 연결 키 발급 (AXcellent 운영팀). 회사마다 키를 하나 발급해 전달한다. 연결은 고객사 시스템 관리자가 이 키를 넣어서 한다.
const STATUS: Record<KeyStatus, { label: string; tone: string }> = {
  issued: { label: '발급됨 · 연결 전', tone: 'bg-amber-50 text-amber-800' },
  connected: { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700' },
  revoked: { label: '폐기', tone: 'bg-ink-100 text-ink-500' },
}
const INDUSTRIES = ['온라인 쇼핑몰', '오프라인 유통', '여행사', '카페 체인', '병원', '제조', '기타']
const mask = (k: string) => `${k.slice(0, 8)}••••-••••`
const day = (iso: string) => new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export default function KeyIssue() {
  const keys = useKeys()
  const current = useCompany()
  const [name, setName] = useState('')
  const [industry, setIndustry] = useState(INDUSTRIES[0])
  const [error, setError] = useState<string | null>(null)
  const [fresh, setFresh] = useState<IssuedKey | null>(null)
  const [copied, setCopied] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return setError('회사 이름을 입력하세요.')
    if (keys.some((k) => k.company.name === name.trim() && k.status !== 'revoked')) return setError('이미 키가 있는 회사예요. 다시 발급하려면 기존 키를 폐기하세요.')
    setError(null)
    setFresh(issueKey(name.trim(), industry))
    setCopied(false)
    setName('')
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
        <p className="mt-1 text-sm text-ink-500">회사마다 키를 하나 발급해 전달해요. 연결은 고객사 시스템 관리자가 이 키를 넣어서 해요.</p>
      </header>

      <section className="card p-6">
        <h2 className="card-title">새 키 발급</h2>
        <form onSubmit={submit} className="mt-4 grid gap-3 md:grid-cols-[2fr_1fr_auto]">
          <input className="field" placeholder="회사 이름 (예: 한결베이커리)" value={name} onChange={(e) => setName(e.target.value)} aria-label="회사 이름" />
          <select className="field" value={industry} onChange={(e) => setIndustry(e.target.value)} aria-label="업종">
            {INDUSTRIES.map((x) => <option key={x}>{x}</option>)}
          </select>
          <button className="btn-primary px-5">발급</button>
        </form>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        {fresh && (
          <div className="mt-4 flex flex-wrap items-center gap-3 border border-dashed border-brand-500 bg-brand-50 px-5 py-4">
            <span className="text-sm text-brand-700">{fresh.company.name} 연결 키</span>
            <span className="select-all font-mono text-lg tracking-wider">{fresh.key}</span>
            <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={() => copy(fresh.key)}>{copied ? '복사됨' : '복사'}</button>
            <span className="w-full text-xs text-ink-500">전체 키는 지금만 보여요. 고객사 담당자에게 전달하세요.</span>
          </div>
        )}
      </section>

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">고객사</th>
              <th className="px-3 py-3 font-medium">키</th>
              <th className="px-3 py-3 font-medium">발급</th>
              <th className="px-3 py-3 font-medium">상태</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {keys.map((k) => (
              <tr key={k.key}>
                <td className="px-5 py-3">
                  <p className="font-medium">{k.company.name}</p>
                  <p className="text-xs text-ink-500">{k.company.industry}</p>
                </td>
                <td className="px-3 py-3 font-mono text-xs">{mask(k.key)}</td>
                <td className="px-3 py-3 text-ink-700">{day(k.issuedAt)}</td>
                <td className="px-3 py-3">
                  <span className={`chip ${STATUS[k.status].tone}`}>{STATUS[k.status].label}</span>
                  {k.status === 'connected' && current?.id === k.company.id && <span className="ml-2 text-xs text-ink-500">이 데모에 연결된 회사</span>}
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
