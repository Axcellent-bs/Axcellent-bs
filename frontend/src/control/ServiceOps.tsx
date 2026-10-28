import { useEffect, useState } from 'react'
import { SERVICES, toggleHidden, useHiddenServices, type Service } from '@/demo/services'
import { COMPANIES } from './data'

// 서비스 관리 (AXcellent 운영팀). 우리가 만든 서비스의 버전·배포·카탈로그 공개를 관리한다.
// 카탈로그에서 내리면 메인 카탈로그와 고객사 서비스 고르기에서 빠진다(이미 쓰는 고객사는 그대로).
// 버전·배포는 데모 예시라 새로고침하면 처음으로 돌아간다.
type Stage = '정식' | '베타'
type Ops = { version: string; next?: string; stage: Stage; using: number; errors: number; released: string }

const OPS: Record<string, Ops> = {
  incident: { version: '0.9.3', stage: '정식', using: 5, errors: 1, released: '10/27' },
  hosts: { version: '0.9.3', stage: '정식', using: 5, errors: 0, released: '10/27' },
  inquiry: { version: '0.9.3', next: '0.9.4', stage: '정식', using: 4, errors: 3, released: '10/27' },
  claims: { version: '0.9.2', next: '0.9.3', stage: '정식', using: 3, errors: 1, released: '10/24' },
  reviews: { version: '0.9.3', stage: '정식', using: 3, errors: 0, released: '10/27' },
  coupon: { version: '0.9.3', stage: '정식', using: 4, errors: 2, released: '10/27' },
  finance: { version: '0.9.3', stage: '정식', using: 3, errors: 0, released: '10/26' },
  data: { version: '0.9.2', next: '0.9.3', stage: '정식', using: 4, errors: 0, released: '10/24' },
  handover: { version: '0.8.1', next: '0.9.0', stage: '베타', using: 2, errors: 0, released: '10/20' },
  decisions: { version: '0.8.1', stage: '베타', using: 1, errors: 0, released: '10/20' },
  planning: { version: '0.9.1', stage: '베타', using: 2, errors: 0, released: '10/23' },
  dev: { version: '0.9.1', stage: '베타', using: 1, errors: 1, released: '10/23' },
  orchestrator: { version: '0.9.3', stage: '정식', using: 5, errors: 0, released: '10/27' },
}

type Rollout = { from: string; to: string; pct: number }

function Mark({ s }: { s: Service }) {
  return (
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center ${s.by === 'DejaFix' ? 'bg-[#1d1d3a]' : 'bg-brand-50 text-brand-600'}`}>
      {s.logo ? <img src={s.logo} alt="" width={20} height={20} /> : <s.Icon size={18} />}
    </span>
  )
}

export default function ServiceOps() {
  const hidden = useHiddenServices()
  const [ops, setOps] = useState(OPS)
  const [rolling, setRolling] = useState<Record<string, Rollout>>({})
  const [prev, setPrev] = useState<Record<string, string>>({})

  // 배포 진행: 고객사에 차례로 퍼진다 (데모용 시간)
  useEffect(() => {
    if (!Object.keys(rolling).length) return
    const t = setInterval(() => {
      setRolling((r) => {
        const next: Record<string, Rollout> = {}
        for (const [id, x] of Object.entries(r)) {
          const pct = Math.min(100, x.pct + 20)
          if (pct >= 100) setOps((o) => ({ ...o, [id]: { ...o[id], version: x.to, next: undefined, released: '오늘' } }))
          else next[id] = { ...x, pct }
        }
        return next
      })
    }, 700)
    return () => clearInterval(t)
  }, [rolling])

  const deploy = (id: string) => {
    const o = ops[id]
    if (!o.next) return
    setPrev((p) => ({ ...p, [id]: o.version }))
    setRolling((r) => ({ ...r, [id]: { from: o.version, to: o.next!, pct: 0 } }))
  }
  const rollback = (id: string) => {
    const to = prev[id]
    if (!to) return
    setOps((o) => ({ ...o, [id]: { ...o[id], next: o[id].version, version: to, released: '되돌림' } }))
    setPrev((p) => {
      const { [id]: _drop, ...rest } = p
      return rest
    })
  }

  const list = Object.values(ops)
  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 pb-12">
      <header>
        <p className="text-sm text-ink-500">관제 센터</p>
        <h1 className="text-2xl font-semibold">서비스 관리</h1>
        <p className="mt-1 text-sm text-ink-500">우리 서비스의 버전과 배포, 카탈로그 공개를 관리해요.</p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">카탈로그 공개</p><p className="mt-1 text-3xl font-bold tabular-nums">{SERVICES.length - hidden.length} / {SERVICES.length}</p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">베타</p><p className="mt-1 text-3xl font-bold tabular-nums">{list.filter((o) => o.stage === '베타').length}</p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">배포할 새 버전</p><p className="mt-1 text-3xl font-bold tabular-nums">{list.filter((o) => o.next).length}</p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">오늘 오류</p><p className={`mt-1 text-3xl font-bold tabular-nums ${list.some((o) => o.errors) ? 'text-red-700' : ''}`}>{list.reduce((a, o) => a + o.errors, 0)}</p></div>
      </div>

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">서비스</th>
              <th className="px-3 py-3 font-medium">버전</th>
              <th className="px-3 py-3 font-medium">단계</th>
              <th className="px-3 py-3 text-right font-medium">쓰는 고객사</th>
              <th className="px-3 py-3 text-right font-medium">오늘 오류</th>
              <th className="px-3 py-3 font-medium">배포</th>
              <th className="px-3 py-3 font-medium">카탈로그</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {SERVICES.map((s) => {
              const o = ops[s.id]
              const r = rolling[s.id]
              const off = hidden.includes(s.id)
              return (
                <tr key={s.id} className={off ? 'bg-app' : ''}>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-3">
                      <Mark s={s} />
                      <div>
                        <p className="font-medium">{s.name}</p>
                        <p className="text-xs text-ink-500">{s.by}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs">{o.version}</span>
                    <span className="ml-2 text-xs text-ink-500">{o.released}</span>
                  </td>
                  <td className="px-3 py-3"><span className={`chip ${o.stage === '정식' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'}`}>{o.stage}</span></td>
                  <td className="px-3 py-3 text-right tabular-nums">{o.using} / {COMPANIES.length}</td>
                  <td className={`px-3 py-3 text-right tabular-nums ${o.errors ? 'font-medium text-red-700' : ''}`}>{o.errors}</td>
                  <td className="w-[230px] px-3 py-3">
                    {r ? (
                      <div className="space-y-1">
                        <div className="h-1.5 bg-ink-100" aria-hidden="true"><div className="h-1.5 bg-brand-600 transition-[width] duration-500" style={{ width: `${r.pct}%` }} /></div>
                        <p className="text-xs text-ink-700">{r.from} → {r.to} 배포 중 · 고객사 {Math.round((r.pct / 100) * o.using)}/{o.using}</p>
                      </div>
                    ) : o.next ? (
                      <button type="button" className="btn-primary px-3 py-1 text-xs" onClick={() => deploy(s.id)}>{o.next} 배포</button>
                    ) : prev[s.id] ? (
                      <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => rollback(s.id)}>{prev[s.id]}로 되돌리기</button>
                    ) : (
                      <span className="text-xs text-ink-500">최신</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <button type="button" role="switch" aria-checked={!off} onClick={() => toggleHidden(s.id)}
                      className={`px-3 py-1 text-xs font-medium ${off ? 'bg-ink-100 text-ink-500' : 'bg-brand-50 text-brand-700'}`}>
                      {off ? '내림' : '공개 중'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-ink-500">카탈로그에서 내리면 메인 카탈로그와 고객사 서비스 고르기에서 빠져요. 이미 쓰고 있는 고객사는 그대로 써요.</p>
    </div>
  )
}
