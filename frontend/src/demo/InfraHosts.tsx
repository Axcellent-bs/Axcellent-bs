import { useEffect, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router'
import { ago } from '@/lib/format'
import { useCompany } from './company'
import { newId, now, privateIp, useHosts, type Host } from './store'

// 사내 PC 연결 (시스템 관리자 /system/hosts, DejaFix 서비스 /hosts 같은 화면).
// PC를 IP로 등록하면 Zabbix에 호스트 등록 → 에이전트 응답 확인 → DejaFix가 그 호스트를 읽기 시작하는 순서로 연결된다.
// 데모라 실제 Zabbix는 부르지 않고 단계만 보여 준다. 사내망 주소만 받아서 시스템이 회사 밖으로 열리지 않는다.
const FLOW = [
  { title: 'Zabbix에 호스트 등록', note: 'host.create · 그룹 Windows · 템플릿 Windows by Zabbix agent active' },
  { title: '에이전트 응답 확인', note: 'PC의 Zabbix agent 2가 첫 데이터를 보냄 (10051)' },
  { title: 'DejaFix가 호스트 인식', note: 'DejaFix 읽기 전용 계정의 host.get에 새 PC가 보임' },
  { title: '연결 완료', note: 'CPU·메모리·디스크 감시 시작, 장애가 나면 DejaFix 장애 대응으로' },
] as const
const FLOW_MS = 1200
const STATUS: Record<Host['status'], { label: string; tone: string; dot: string }> = {
  online: { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
  offline: { label: '응답 없음', tone: 'bg-red-50 text-red-700', dot: 'bg-red-500' },
  pending: { label: '연결 확인 중', tone: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500' },
}

export default function InfraHosts() {
  const [hosts, setHosts] = useHosts()
  const company = useCompany()
  const [form, setForm] = useState({ dept: '', hostname: '', ip: '' })
  // 팀 이름은 회사가 정한다. 이미 쓰는 이름을 제안만 하고 새 이름도 받는다
  const teams = [...new Set(hosts.map((h) => h.dept))]
  const [error, setError] = useState<string | null>(null)
  const { pathname } = useLocation()
  const asAdmin = pathname.startsWith('/system')
  // 지금 연결 중인 PC와 단계
  const [flow, setFlow] = useState<{ id: string; name: string; step: number } | null>(null)
  // 데모용 Zabbix 서버 주소: 회사 사내망의 .5
  const zabbixHost = company ? company.network.replace(/\.\d+\/\d+$/, '.5') : '<Zabbix 서버>'

  const online = hosts.filter((h) => h.status === 'online').length
  const update = (id: string, patch: Partial<Host>) => setHosts((list) => list.map((h) => (h.id === id ? { ...h, ...patch } : h)))
  const check = (h: Host) => {
    update(h.id, { status: 'pending' })
    setTimeout(() => update(h.id, { status: 'online', lastSeen: now() }), 900)
  }

  const add = (e: FormEvent) => {
    e.preventDefault()
    if (!form.dept.trim()) return setError('사용하는 팀이나 위치를 입력하세요.')
    if (!form.hostname.trim()) return setError('PC 이름을 입력하세요.')
    if (!privateIp(form.ip)) return setError('사내망 IP만 등록할 수 있습니다 (10.x, 172.16~31.x, 192.168.x).')
    if (hosts.some((h) => h.ip === form.ip.trim())) return setError('이미 등록된 IP입니다.')
    const h: Host = { id: newId(), dept: form.dept.trim(), hostname: form.hostname.trim(), ip: form.ip.trim(), status: 'pending', agent: '설치 확인 중', lastSeen: now() }
    setHosts((list) => [...list, h])
    setError(null)
    setForm({ ...form, hostname: '', ip: '' })
    setFlow({ id: h.id, name: h.hostname, step: 0 })
  }

  useEffect(() => {
    if (!flow) return
    if (flow.step >= FLOW.length - 1) {
      update(flow.id, { status: 'online', agent: 'Zabbix agent 2 · DejaFix 감시 중', lastSeen: now() })
      const t = setTimeout(() => setFlow(null), 2500)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setFlow((f) => (f ? { ...f, step: f.step + 1 } : f)), FLOW_MS)
    return () => clearTimeout(t)
  }, [flow]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-500">{asAdmin ? '시스템 관리' : 'DejaFix'}</p>
          <h1 className="text-2xl font-semibold">{asAdmin ? '사내 PC 연결' : 'PC·서버 연결'}</h1>
          <p className="mt-1 text-sm text-ink-500">회사 PC와 서버를 사내 IP로 연결하고 연결 상태를 챙깁니다.</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {company && <span className="chip bg-ink-100 text-ink-700">사내망 {company.network}</span>}
          <span className="chip bg-emerald-50 text-emerald-700">외부 접근 차단</span>
          <span className="chip bg-brand-50 text-brand-700">연결 {online}/{hosts.length}대</span>
        </div>
      </header>

      <section className="card p-6">
        <div className="flex flex-col items-center gap-5">
          <div className="rounded-2xl bg-brand-600 px-6 py-3 text-center text-white">
            <p className="font-semibold">DejaFix</p>
            <p className="text-xs text-white/70">{company ? `${company.name} 사내 서버` : '연결된 고객사 없음'}</p>
          </div>
          <div className="h-5 w-px bg-ink-300" aria-hidden="true" />
          <ul className="grid w-full grid-cols-2 gap-3 md:grid-cols-3">
            {hosts.map((h) => (
              <li key={h.id} className="rounded-2xl border border-ink-100 bg-app p-3 text-sm">
                <p className="flex items-center gap-1.5 font-medium">
                  <span className={`h-2 w-2 rounded-full ${STATUS[h.status].dot}`} aria-hidden="true" />
                  {h.dept}
                </p>
                <p className="mt-1 font-mono text-xs text-ink-500">{h.ip}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-6 py-3 font-medium">팀·위치</th>
              <th className="px-3 py-3 font-medium">PC 이름</th>
              <th className="px-3 py-3 font-medium">IP</th>
              <th className="px-3 py-3 font-medium">설치</th>
              <th className="px-3 py-3 font-medium">상태</th>
              <th className="px-3 py-3 font-medium">마지막 응답</th>
              <th className="px-6 py-3" />
            </tr>
          </thead>
          <tbody>
            {hosts.map((h) => (
              <tr key={h.id} className="border-t border-ink-100">
                <td className="px-6 py-3 font-medium">{h.dept}</td>
                <td className="px-3 py-3">{h.hostname}</td>
                <td className="px-3 py-3 font-mono text-xs">{h.ip}</td>
                <td className="px-3 py-3 text-ink-700">{h.agent}</td>
                <td className="px-3 py-3"><span className={`chip ${STATUS[h.status].tone}`}>{STATUS[h.status].label}</span></td>
                <td className="px-3 py-3 text-ink-500">{ago(h.lastSeen)}</td>
                <td className="px-6 py-3 text-right">
                  <div className="flex justify-end gap-1">
                    <button type="button" className="btn-quiet" onClick={() => check(h)}>연결 확인</button>
                    <button type="button" className="btn-quiet" onClick={() => setHosts((list) => list.filter((x) => x.id !== h.id))}>해제</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {flow && (
        <section className="card space-y-3 p-6" aria-live="polite">
          <h2 className="card-title">{flow.name} 연결 중</h2>
          <ol className="space-y-2">
            {FLOW.map((f, i) => {
              const done = i < flow.step || (i === flow.step && flow.step === FLOW.length - 1)
              const nowStep = i === flow.step && !done
              return (
                <li key={f.title} className={`flex flex-wrap items-center gap-3 rounded-xl px-4 py-2.5 text-sm ${nowStep ? 'bg-brand-50 font-semibold text-brand-700' : done ? 'text-ink-900' : 'text-ink-500'}`}>
                  <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${done ? 'bg-emerald-700 text-white' : nowStep ? 'animate-pulse bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>{done ? '✓' : i + 1}</span>
                  {f.title}
                  <span className="ml-auto text-xs font-normal text-ink-500">{f.note}</span>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      <section className="card p-6">
        <h2 className="card-title">PC 추가</h2>
        <div className="mt-3 space-y-1 rounded-2xl bg-app p-4 text-sm">
          <p className="font-medium text-ink-900">1. 그 PC에 Zabbix agent 2를 설치해요</p>
          <p className="break-all rounded-xl bg-[#1d1d3a] px-3 py-2 font-mono text-xs text-[#e4e4f4]">
            msiexec /i zabbix_agent2-7.0-windows-amd64.msi /qn SERVER={zabbixHost} SERVERACTIVE={zabbixHost} HOSTNAME={form.hostname.trim() || '<PC 이름>'}
          </p>
          <p className="pt-1 font-medium text-ink-900">2. 아래에 PC 이름과 IP를 넣고 연결하면 Zabbix 등록부터 DejaFix 연결까지 이어서 해요</p>
        </div>
        <form onSubmit={add} className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]">
          <input className="field" list="host-teams" placeholder="사용하는 팀·위치 (예: 3층 CS실)" value={form.dept} onChange={(e) => setForm({ ...form, dept: e.target.value })} aria-label="사용하는 팀·위치" />
          <datalist id="host-teams">{teams.map((t) => <option key={t} value={t} />)}</datalist>
          <input className="field" placeholder="PC 이름 (예: mkt-pc-02)" value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} aria-label="PC 이름" />
          <input className="field font-mono" placeholder="사내 IP (예: 192.168.10.42)" value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value })} aria-label="IP" />
          <button className="btn-primary" disabled={!!flow}>연결</button>
        </form>
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </section>
    </div>
  )
}
