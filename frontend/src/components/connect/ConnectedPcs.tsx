import { useCallback, useEffect, useState } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { currentIncidents, recentIncidents } from '@/lib/dejafix'
import { connectedPcs, pcSummary, type PcRow } from '@/lib/connectedPcs'
import { when } from '@/lib/toolForms'

// 회원·서버 관리 › DejaFix 연결 탭: "연결한 PC".
// DejaFix에는 호스트 목록 API가 없어서 최근 장애·열린 장애에 나온 호스트를 모은다 (판정은 lib/connectedPcs.ts).
// 연결 현황만 보여 준다(장애 제목·건수는 장애 대응 탭 몫). "연결 확인"은 두 목록을 DejaFix에서 다시 읽는다.
// 못 읽으면 표 대신 이유를 보여 준다. 0대로 지어내지 않는다.

type Load = { rows: PcRow[]; checkedAt: string } | { error: string }

/** 두 목록 읽기. busy = 지금 읽는 버튼('all' 또는 PC 이름) */
function usePcs() {
  const [load, setLoad] = useState<Load | null>(null)
  const [busy, setBusy] = useState<string | null>('all')
  const read = useCallback(async (who: string) => {
    setBusy(who)
    try {
      const [recent, open] = await Promise.all([recentIncidents(), currentIncidents()])
      setLoad({ rows: connectedPcs(recent.data, open.data), checkedAt: new Date().toISOString() })
    } catch (e) {
      setLoad({ error: (e as Error).message || 'DejaFix가 응답하지 않았어요.' })
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

      {load && 'error' in load && (
        <div className="space-y-3">
          <Notice tone="bad">DejaFix에서 PC 목록을 읽을 수 없어요: {load.error}</Notice>
          <button type="button" className="btn-ghost" disabled={busy !== null} onClick={() => void read('all')}>{busy ? '확인 중…' : '다시 읽기'}</button>
        </div>
      )}

      {ok && sum && <>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="text-lg font-semibold">{sum.connected.toLocaleString('ko-KR')} / {sum.total.toLocaleString('ko-KR')}곳 연결됨</p>
          <div className="h-2 w-40 bg-ink-100" role="img" aria-label={`연결 비율 ${sum.pct}%`}>
            <div className="h-full bg-emerald-700" style={{ width: `${sum.pct}%` }} />
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <span className="text-xs text-ink-500">마지막 확인 {when(ok.checkedAt)}</span>
            <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => void read('all')}>{busy === 'all' ? '확인 중…' : '전체 연결 확인'}</button>
          </div>
        </div>

        {ok.rows.length === 0
          ? <p className="text-sm text-ink-500">DejaFix 장애 기록에 나온 PC가 아직 없어요.</p>
          : (
            <div className="overflow-x-auto border border-ink-100">
              <table className="w-full whitespace-nowrap text-sm">
                <thead className="bg-app text-left text-xs text-ink-500">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">PC 이름</th>
                    <th className="px-4 py-2.5 font-medium">연결 상태</th>
                    <th className="px-4 py-2.5 font-medium"><span className="sr-only">동작</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {ok.rows.map((r) => (
                    <tr key={r.name}>
                      <td className="px-4 py-2.5 font-mono">{r.name}</td>
                      <td className="px-4 py-2.5">
                        {r.state === 'up'
                          ? <StatusChip tone="ok">연결됨</StatusChip>
                          : <span className="inline-flex items-center gap-2"><StatusChip tone="bad">응답 없음</StatusChip><span className="text-xs text-ink-500">{when(r.since)}부터</span></span>}
                      </td>
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

      <p className="text-xs text-ink-500">연결 상태는 Zabbix가 agent 응답을 받고 있는지로 정해요. '연결 확인'은 DejaFix에서 지금 상태를 다시 읽어요. 장애 기록에 한 번도 나온 적 없는 PC는 목록에 없을 수 있어요.</p>
    </section>
  )
}
