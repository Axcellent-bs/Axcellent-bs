import { PageHead, SectionHead, StatusChip, Tiles } from '@/components/connect/parts'
import { AGENTS, FAMILIES, agentName, isRequired, toggleService, useEnabledServices, usePublishedServices, type Service } from './services'

// 서비스 고르기: Adobe Creative Cloud 앱 목록처럼 회사가 쓸 서비스를 켜고 끈다. 끈 서비스는 모든 직원 메뉴에서 빠진다.
// 계층: 요약(사용 중·필수·끈 서비스) › 제품군 구역(인프라·고객 경험…) › 서비스 카드 › 맡은 AI 표(펼침)
function ServiceMark({ s }: { s: Service }) {
  return (
    <span className={`flex h-11 w-11 shrink-0 items-center justify-center ${s.by === 'DejaFix' ? 'bg-[#1d1d3a]' : 'bg-brand-50 text-brand-600'}`}>
      {s.logo ? <img src={s.logo} alt="" width={24} height={24} /> : <s.Icon size={22} />}
    </span>
  )
}

function ServiceCard({ s, on }: { s: Service; on: boolean }) {
  const required = isRequired(s.id)
  return (
    <li className={`card flex flex-col ${on ? '' : 'bg-ink-50/60'}`}>
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div className="flex items-start gap-3">
          <ServiceMark s={s} />
          <div className="min-w-0 flex-1">
            <p className={`text-[15px] font-semibold ${on ? 'text-ink-900' : 'text-ink-500'}`}>{s.name}</p>
            <p className="text-xs text-ink-500">{agentName(s) ?? s.by}</p>
          </div>
          {required ? <StatusChip tone="ok">필수</StatusChip> : on ? <StatusChip tone="ok">사용 중</StatusChip> : <StatusChip tone="off">꺼짐</StatusChip>}
        </div>
        <p className={`text-sm leading-6 ${on ? 'text-ink-700' : 'text-ink-500'}`}>{s.desc}</p>
        {s.children && (
          <p className="text-xs text-ink-500">
            화면: {s.children.filter((c) => !c.soon).map((c) => c.label).join(', ')}
          </p>
        )}
      </div>
      <div className="flex items-center justify-end gap-3 border-t border-ink-100 px-5 py-3">
        {required ? (
          <span className="text-xs text-ink-500">다른 AI가 장애 기록을 써서 늘 켜 둬요</span>
        ) : (
          <button type="button" onClick={() => toggleService(s.id)} className={on ? 'btn-quiet' : 'btn-primary'}>
            {on ? '끄기' : '켜기'}
          </button>
        )}
      </div>
    </li>
  )
}

export default function ServiceCatalog() {
  const enabled = useEnabledServices()
  // AXcellent가 카탈로그에서 내린 서비스는 새로 고를 수 없어서 목록에서 뺀다
  const SERVICES = usePublishedServices()
  const usedCount = SERVICES.filter((s) => enabled.includes(s.id)).length
  const required = SERVICES.filter((s) => isRequired(s.id))
  const offCount = SERVICES.length - usedCount

  return (
    <div className="space-y-10 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="서비스 고르기">
        회사에서 쓸 서비스만 켜요. 끈 서비스는 모든 직원 메뉴에서 빠지고, 맡은 AI도 그 일을 사람 할 일함으로 넘겨요.
      </PageHead>

      <Tiles label="서비스 요약" items={[
        { name: '사용 중', value: `${usedCount} / ${SERVICES.length}개`, tone: 'ok', note: '직원 메뉴에 보이는 서비스' },
        { name: '꺼 둔 서비스', value: `${offCount}개`, tone: offCount ? 'off' : 'ok', note: offCount ? '켜면 바로 메뉴에 생겨요' : '모두 켜져 있어요' },
        { name: '필수', value: `${required.length}개`, tone: 'ok', note: required.map((s) => s.name).join(', ') },
        { name: '늘 포함', value: 'AI 대화방 · AI 오피스', note: '고르지 않아도 모든 직원이 써요' },
      ]} />

      {FAMILIES.map((f) => {
        const list = SERVICES.filter((s) => s.family === f.id)
        if (!list.length) return null
        const on = list.filter((s) => enabled.includes(s.id)).length
        return (
          <section key={f.id} className="space-y-4">
            <SectionHead title={f.name} desc={f.desc} right={<span className="text-sm text-ink-500">{on} / {list.length}개 사용 중</span>} />
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {list.map((s) => <ServiceCard key={s.id} s={s} on={enabled.includes(s.id)} />)}
            </ul>
          </section>
        )
      })}

      <section className="space-y-3">
        <SectionHead title="서비스를 맡은 AI" desc="서비스마다 맡은 AI가 있어요. 끈 서비스는 줄을 그어 보여 줘요. AI는 모두 AXcellent가 관리해요." />
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-ink-100 text-left text-xs text-ink-500">
              <tr>
                <th className="px-5 py-2.5 font-medium">AI</th>
                <th className="px-3 py-2.5 font-medium">맡은 서비스</th>
                <th className="px-5 py-2.5 font-medium">연결 서버</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {AGENTS.map((g) => (
                <tr key={g.id}>
                  <td className="px-5 py-3 font-medium text-ink-900">{g.name}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-1">
                      {SERVICES.filter((s) => s.agent === g.id).map((s) => (
                        <span key={s.id} className={`chip ${enabled.includes(s.id) ? 'bg-brand-50 text-brand-700' : 'bg-ink-100 text-ink-500 line-through'}`}>{s.name}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-ink-500">{g.backend}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
