import { useEffect, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router'
import { ago } from '@/lib/format'
import { useCompany } from './company'
import { useProfiles } from './members'
import { customerAccounts } from './session'
import { newId, now, privateIp, useHosts, type Host } from './store'
import { PageHead, SectionHead, StatusChip, type Tone } from '@/components/connect/parts'

// 사내 PC 연결 (시스템 관리자 회원·서버 관리 /system/members?tab=pcs, DejaFix 서비스 /hosts 같은 화면).
// PC를 IP로 등록하면 Zabbix에 호스트 등록 → 에이전트 응답 확인 → DejaFix가 그 호스트를 읽기 시작하는 순서로 연결된다.
// 데모라 실제 Zabbix는 부르지 않고 단계만 보여 준다. 사내망 주소만 받아서 시스템이 회사 밖으로 열리지 않는다.
const FLOW = [
  { title: 'Zabbix에 호스트 등록', note: 'host.create · 그룹 Windows · 템플릿 Windows by Zabbix agent active' },
  { title: '에이전트 응답 확인', note: 'PC의 Zabbix agent 2가 첫 데이터를 보냄 (10051)' },
  { title: 'DejaFix가 호스트 인식', note: 'DejaFix 읽기 전용 계정의 host.get에 새 PC가 보임' },
  { title: '연결 완료', note: 'CPU·메모리·디스크 감시 시작, 장애가 나면 DejaFix 장애 대응으로' },
] as const
const FLOW_MS = 1200
const STATUS: Record<Host['status'], { label: string; tone: Tone; dot: string }> = {
  online: { label: '연결됨', tone: 'ok', dot: 'bg-emerald-700' },
  offline: { label: '응답 없음', tone: 'bad', dot: 'bg-red-700' },
  pending: { label: '연결 확인 중', tone: 'wait', dot: 'bg-amber-700' },
}

/** embedded: 회원·서버 관리(/system/members)의 PC·서버 탭 안에 넣을 때. 페이지 제목 없이 내용만 그린다 */
export default function InfraHosts({ embedded = false }: { embedded?: boolean }) {
  const [hosts, setHosts] = useHosts()
  const company = useCompany()
  useProfiles() // 회원 부서를 바꾸면 쓰는 사람 칸의 부서 표시를 다시 그린다
  const accounts = customerAccounts()
  const [form, setForm] = useState({ dept: '', hostname: '', ip: '', owner: '' })
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
    const h: Host = { id: newId(), dept: form.dept.trim(), hostname: form.hostname.trim(), ip: form.ip.trim(), status: 'pending', agent: '설치 확인 중', lastSeen: now(), ...(form.owner ? { owner: form.owner } : {}) }
    setHosts((list) => [...list, h])
    setError(null)
    setForm({ ...form, hostname: '', ip: '', owner: '' })
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
    <div className={embedded ? 'space-y-8' : 'space-y-8 px-4 py-8 sm:px-8'}>
      {!embedded && (
        <PageHead where={asAdmin ? '시스템 관리' : 'DejaFix'} title="PC·서버 연결">회사 PC와 서버를 사내 IP로 연결하고 연결 상태를 챙겨요.</PageHead>
      )}

      <section className="space-y-4">
        <SectionHead title="PC·서버" desc="DejaFix가 감시하는 회사 PC와 서버예요. 사내 IP만 받아서 회사 밖에서는 열리지 않아요."
          right={<>
            <StatusChip tone={hosts.length === 0 ? 'off' : online === hosts.length ? 'ok' : 'wait'}>연결 {online} / {hosts.length}대</StatusChip>
            {company && <span className="text-xs text-ink-500">사내망 <span className="font-mono">{company.network}</span></span>}
          </>} />
        <div className="card p-6">
          <div className="flex flex-col items-center gap-4">
            <div className="bg-brand-600 px-6 py-3 text-center text-white">
              <p className="font-semibold">DejaFix</p>
              <p className="text-xs text-white/70">{company ? `${company.name} 사내 서버` : '연결된 고객사 없음'}</p>
            </div>
            <div className="h-5 w-px bg-ink-300" aria-hidden="true" />
            <ul className="grid w-full grid-cols-2 gap-px border border-ink-100 bg-ink-100 md:grid-cols-3 lg:grid-cols-4">
              {hosts.map((h) => (
                <li key={h.id} className="bg-surface px-4 py-3 text-sm">
                  <p className="flex items-center gap-1.5 font-medium">
                    <span className={`h-2 w-2 ${STATUS[h.status].dot}`} aria-hidden="true" />
                    {h.dept}
                  </p>
                  <p className="mt-1 font-mono text-xs text-ink-500">{h.ip}</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <SectionHead title="PC 추가" desc="그 PC에 Zabbix agent 2를 설치한 뒤, PC 이름과 IP를 넣으면 Zabbix 등록부터 DejaFix 연결까지 이어서 해요." />
        <div className="card p-6">
        <div className="space-y-1 bg-ink-50 p-4 text-sm">
          <p className="font-medium text-ink-900">1. 그 PC에 Zabbix agent 2를 설치해요</p>
          <p className="break-all rounded-xl bg-[#1d1d3a] px-3 py-2 font-mono text-xs text-[#e4e4f4]">
            msiexec /i zabbix_agent2-7.0-windows-amd64.msi /qn SERVER={zabbixHost} SERVERACTIVE={zabbixHost} HOSTNAME={form.hostname.trim() || '<PC 이름>'}
          </p>
          <p className="pt-1 font-medium text-ink-900">2. 아래에 PC 이름과 IP를 넣고 연결하면 Zabbix 등록부터 DejaFix 연결까지 이어서 해요</p>
        </div>
        <form onSubmit={add} className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
          <input className="field" list="host-teams" placeholder="사용하는 팀·위치 (예: 3층 CS실)" value={form.dept} onChange={(e) => setForm({ ...form, dept: e.target.value })} aria-label="사용하는 팀·위치" />
          <datalist id="host-teams">{teams.map((t) => <option key={t} value={t} />)}</datalist>
          <input className="field" placeholder="PC 이름 (예: mkt-pc-02)" value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} aria-label="PC 이름" />
          <select className="field" value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} aria-label="쓰는 사람">
            <option value="">쓰는 사람: 공용</option>
            {accounts.map((a) => <option key={a.email} value={a.email}>{a.name}{a.dept ? ` (${a.dept})` : ''}</option>)}
          </select>
          <input className="field font-mono" placeholder="사내 IP (예: 192.168.10.42)" value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value })} aria-label="IP" />
          <button className="btn-primary" disabled={!!flow}>연결</button>
        </form>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        </div>
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

      <section className="space-y-4">
      <SectionHead title="연결한 PC" desc="PC마다 쓰는 사람을 고르면 회원 탭의 쓰는 PC에 나와요." />
      <div className="card overflow-x-auto">
        <table className="w-full whitespace-nowrap text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-6 py-3 font-medium">팀·위치</th>
              <th className="px-3 py-3 font-medium">PC 이름</th>
              <th className="px-3 py-3 font-medium">쓰는 사람</th>
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
                <td className="px-3 py-3">
                  <select className="field w-36 py-1 text-xs" aria-label={`${h.hostname} 쓰는 사람`} value={h.owner ?? ''}
                    onChange={(e) => update(h.id, { owner: e.target.value || undefined })}>
                    <option value="">공용</option>
                    {h.owner && !accounts.some((a) => a.email === h.owner) && <option value={h.owner}>{h.owner}</option>}
                    {accounts.map((a) => <option key={a.email} value={a.email}>{a.name}{a.dept ? ` (${a.dept})` : ''}</option>)}
                  </select>
                </td>
                <td className="px-3 py-3 font-mono text-xs">{h.ip}</td>
                <td className="px-3 py-3 text-ink-700">{h.agent}</td>
                <td className="px-3 py-3"><StatusChip tone={STATUS[h.status].tone}>{STATUS[h.status].label}</StatusChip></td>
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
      </div>
      </section>
    </div>
  )
}
