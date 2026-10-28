import { ACCOUNTS } from './session'
import { SERVICES as ALL_SERVICES, toggleService, useEnabledServices, usePublishedServices, type Service } from './services'

// 서비스 고르기: Adobe Creative Cloud 앱 목록처럼 회사가 쓸 서비스를 켜고 끈다. 끈 서비스는 모든 직원 메뉴에서 빠진다.
// 직원에게 어떤 서비스를 줄지는 계정마다 배정한다(아래 표). 부서는 두지 않는다.
function ServiceMark({ s }: { s: Service }) {
  return (
    <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl ${s.by === 'DejaFix' ? 'bg-[#1d1d3a]' : 'bg-brand-50 text-brand-600'}`}>
      {s.logo ? <img src={s.logo} alt="" width={26} height={26} /> : <s.Icon size={24} />}
    </span>
  )
}

export default function ServiceCatalog() {
  const enabled = useEnabledServices()
  // AXcellent가 카탈로그에서 내린 서비스는 새로 고를 수 없어서 목록에서 뺀다
  const SERVICES = usePublishedServices()
  const users = (id: string) => ACCOUNTS.filter((a) => a.services.includes(id)).length

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-500">시스템 관리</p>
          <h1 className="text-2xl font-semibold">서비스 고르기</h1>
          <p className="mt-1 text-sm text-ink-500">회사에서 쓸 서비스만 켭니다. 끈 서비스는 직원 메뉴에서 빠집니다.</p>
        </div>
        <span className="chip bg-brand-50 text-brand-700">사용 중 {enabled.length} / {SERVICES.length}</span>
      </header>

      <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {SERVICES.map((s) => {
          const on = enabled.includes(s.id)
          return (
            <li key={s.id} className={`card flex flex-col gap-4 p-5 ${on ? '' : 'opacity-70'}`}>
              <div className="flex items-start gap-3">
                <ServiceMark s={s} />
                <div className="min-w-0">
                  <p className="card-title">{s.name}</p>
                  <p className="text-xs text-ink-500">{s.by}</p>
                </div>
                {on && <span className="chip ml-auto bg-emerald-50 text-emerald-700">사용 중</span>}
              </div>
              <p className="text-sm text-ink-700">{s.desc}</p>
              {s.children && (
                <div className="flex flex-wrap gap-1">
                  {s.children.filter((c) => !c.soon).map((c) => <span key={c.href} className="chip bg-ink-100 text-ink-700">{c.label}</span>)}
                </div>
              )}
              <div className="mt-auto flex items-center justify-between gap-3 border-t border-ink-100 pt-3">
                <span className="text-xs text-ink-500">배정된 직원 {users(s.id)}명</span>
                <button type="button" onClick={() => toggleService(s.id)} className={on ? 'btn-ghost' : 'btn-primary'}>
                  {on ? '빼기' : '추가하기'}
                </button>
              </div>
            </li>
          )
        })}
      </ul>

      <section className="card p-6">
        <h2 className="card-title">기본 포함</h2>
        <p className="mt-1 text-sm text-ink-500">고르지 않아도 모든 직원이 씁니다.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="chip bg-ink-100 text-ink-700">AI 대화방</span>
          <span className="chip bg-ink-100 text-ink-700">AI 오피스</span>
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="px-6 pt-6">
          <h2 className="card-title">직원별 서비스</h2>
          <p className="mt-1 text-sm text-ink-500">부서 대신 직원마다 쓸 서비스를 배정합니다. 회사가 끈 서비스는 흐리게 보입니다.</p>
        </div>
        <table className="mt-4 w-full text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-6 py-3 font-medium">직원 (예시)</th>
              <th className="px-3 py-3 font-medium">계정</th>
              <th className="px-3 py-3 font-medium">배정된 서비스</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {ACCOUNTS.map((a) => (
              <tr key={a.id}>
                <td className="px-6 py-3 font-medium">{a.role}</td>
                <td className="px-3 py-3 text-ink-500">{a.email}</td>
                <td className="px-3 py-3">
                  <div className="flex flex-wrap gap-1">
                    {a.services.map((id) => {
                      const s = ALL_SERVICES.find((x) => x.id === id)
                      if (!s) return null
                      return (
                        <span key={id} className={`chip ${enabled.includes(id) ? 'bg-brand-50 text-brand-700' : 'bg-ink-100 text-ink-500 line-through'}`}>{s.name}</span>
                      )
                    })}
                    {a.admin && <span className="chip bg-ink-100 text-ink-700">시스템 관리</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
