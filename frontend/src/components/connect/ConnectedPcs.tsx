import { useCallback, useEffect, useState } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { DejafixNotConnectedError, currentIncidents, hostList, recentIncidents } from '@/lib/dejafix'
import { connectedPcs, fromHosts, pcSummary, type PcLoad } from '@/lib/connectedPcs'
import { when } from '@/lib/toolForms'

// 회원·서버 관리 › DejaFix 연결 탭: "연결한 PC".
// DejaFix 호스트 목록(`GET /api/hosts`)으로 Zabbix 감시 호스트 전부와 에이전트 연결 상태를 보여 준다.
// DejaFix가 아직 그 API를 주지 않으면 최근 장애·열린 장애에 나온 호스트만 모으고, 그때는 응답 없음만
// 알 수 있어 나머지는 "확인할 수 없음"이다(판정은 lib/connectedPcs.ts). 장애가 없다고 연결됨으로 보지 않는다.
// 못 읽으면 표 대신 이유를 보여 준다. 0대로 지어내지 않는다.

type Load = PcLoad

/** DejaFix에서 연결한 PC 읽기. busy = 지금 읽는 버튼('all' 또는 PC 이름). 회사 연결 지도도 쓴다 */
export function usePcs() {
  const [load, setLoad] = useState<Load | null>(null)
  const [busy, setBusy] = useState<string | null>('all')
  const read = useCallback(async (who: string) => {
    setBusy(who)
    try {
      const hosts = await hostList()
      if (hosts) {
        setLoad({ rows: fromHosts(hosts.data), checkedAt: hosts.meta.collected_at ?? new Date().toISOString(), fromIncidents: false })
      } else {
        const [recent, open] = await Promise.all([recentIncidents(), currentIncidents()])
        setLoad({ rows: connectedPcs(recent.data, open.data), checkedAt: new Date().toISOString(), fromIncidents: true })
      }
    } catch (e) {
      setLoad(e instanceof DejafixNotConnectedError ? { notConnected: true } : { error: (e as Error).message || 'DejaFix가 응답하지 않았어요.' })
    } finally {
      setBusy(null)
    }
  }, [])
  useEffect(() => { void read('all') }, [read])
  return { load, busy, read }
}

export function ConnectedPcs() {
  const { load, busy, read } = usePcs()
  const ok = load && 'rows' in load ? load : null
  const sum = ok ? pcSummary(ok.rows) : null

  return (
    <section className="card space-y-4 p-5" aria-label="연결한 PC">
      <SectionHead as="h3" title="연결한 PC" desc="Zabbix agent로 DejaFix에 연결된 회사 PC와 서버예요." />

      {!load && <p className="text-sm text-ink-500">DejaFix에서 PC 목록을 읽는 중…</p>}

      {load && 'notConnected' in load && (
        <div className="space-y-3">
          <Notice tone="wait">DejaFix를 아직 연결하지 않았어요. 위 DejaFix 연결에서 연결하면 PC 목록이 보여요.</Notice>
          <button type="button" className="btn-ghost" disabled={busy !== null} onClick={() => void read('all')}>{busy ? '확인 중…' : '다시 읽기'}</button>
        </div>
      )}

      {load && 'error' in load && (
        <div className="space-y-3">
          <Notice tone="bad">DejaFix에서 PC 목록을 읽을 수 없어요: {load.error}</Notice>
          <button type="button" className="btn-ghost" disabled={busy !== null} onClick={() => void read('all')}>{busy ? '확인 중…' : '다시 읽기'}</button>
        </div>
      )}

      {ok && sum && <>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="text-lg font-semibold">
            {sum.connected.toLocaleString('ko-KR')} / {sum.total.toLocaleString('ko-KR')}곳 연결됨
            {sum.unknown > 0 && <span className="ml-2 text-sm font-normal text-ink-500">확인할 수 없음 {sum.unknown.toLocaleString('ko-KR')}곳</span>}
          </p>
          <div className="h-2 w-40 bg-ink-100" role="img" aria-label={`연결 비율 ${sum.pct}%`}>
            <div className="h-full bg-emerald-700" style={{ width: `${sum.pct}%` }} />
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <span className="text-xs text-ink-500">마지막 확인 {when(ok.checkedAt)}</span>
            <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => void read('all')}>{busy === 'all' ? '확인 중…' : '전체 연결 확인'}</button>
          </div>
        </div>

        {ok.fromIncidents && (
          <Notice tone="info">이 DejaFix는 아직 호스트 목록을 주지 않아서, 장애 기록에 나온 PC만 보이고 연결 여부는 '응답 없음'만 알 수 있어요. DejaFix를 새 버전으로 올리면 모든 PC의 연결 상태가 보여요.</Notice>
        )}
        {ok.rows.length === 0
          ? <p className="text-sm text-ink-500">{ok.fromIncidents ? 'DejaFix 장애 기록에 나온 PC가 아직 없어요.' : 'Zabbix가 감시하는 PC가 아직 없어요.'}</p>
          : (
            <div className="overflow-x-auto border border-ink-100">
              <table className="w-full whitespace-nowrap text-sm">
                <thead className="bg-app text-left text-xs text-ink-500">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">PC 이름</th>
                    <th className="px-4 py-2.5 font-medium">연결 상태</th>
                    {!ok.fromIncidents && <th className="px-4 py-2.5 font-medium">열린 장애</th>}
                    <th className="px-4 py-2.5 font-medium"><span className="sr-only">동작</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {ok.rows.map((r) => (
                    <tr key={r.name}>
                      <td className="px-4 py-2.5 font-mono">{r.name}</td>
                      <td className="px-4 py-2.5">
                        {r.state === 'up' && <StatusChip tone="ok">연결됨</StatusChip>}
                        {r.state === 'unknown' && <StatusChip tone="off">확인할 수 없음</StatusChip>}
                        {r.state === 'down' && (
                          <span className="inline-flex items-center gap-2"><StatusChip tone="bad">응답 없음</StatusChip>{r.since && <span className="text-xs text-ink-500">{when(r.since)}부터</span>}</span>
                        )}
                      </td>
                      {!ok.fromIncidents && <td className="px-4 py-2.5 tabular-nums">{r.problems === null ? '—' : `${r.problems.toLocaleString('ko-KR')}건`}</td>}
                      <td className="px-4 py-2.5 text-right">
                        <button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy !== null} onClick={() => void read(r.name)}>{busy === r.name ? '확인 중…' : '연결 확인'}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </>}

      <p className="text-xs text-ink-500">연결 상태는 Zabbix가 agent 응답을 받고 있는지로 정해요(장애가 없다고 연결됨으로 보지 않아요). '연결 확인'은 DejaFix에서 지금 상태를 다시 읽어요.</p>
    </section>
  )
}
