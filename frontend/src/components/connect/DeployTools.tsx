import { useCallback, useEffect, useState } from 'react'
import { Facts, Notice, SectionHead, TONE_DOT } from './parts'
import { ConnectedTool, ToolForm } from './ToolForm'
import { listLatestDeploys, type ToolList } from '@/lib/toolConnections'
import { DEPLOY_CHOICES, appChoices, deployRows, latestDeployView, type LatestDeploys } from '@/lib/toolForms'
import { useVisibleInterval } from '@/lib/visibleInterval'

// 인프라가 배포 도구에서 이력을 받는 주기(deploy_poll 기본 120초)와 같게 다시 읽는다
const LATEST_POLL_MS = 120_000

// 도구 연결 › 배포: 인프라 AI가 장애 때 승인된 롤백을 다시 배포하는 도구(GitHub Actions·Jenkins).
// 맨 위 새 연결 하나(도구 고르기 → 그 도구 칸 → 연결 확인 → 저장), 아래는 연결된 배포 도구만.
// 한 서비스에 배포 도구는 하나 (두 번째는 서버가 409로 막는다). 관리자만 바꾸고 일반 회원은 상태만 본다.
// 연결마다 "최근 배포 <버전> · <시각>": 인프라가 도구에서 받아 둔 성공 배포(dashboard-api /deployments/latest).
// 기록이 없으면 "아직 받은 배포 기록 없음", 못 읽으면 "확인할 수 없음" + 이유 (지어내지 않는다).
// 화면이 보이는 동안 2분마다 다시 읽는다(가려져 있으면 쉬고, 앞 요청이 오는 중이면 건너뛴다).
export function DeployTools({ data, admin, onChanged }: { data: ToolList; admin: boolean; onChanged: () => void }) {
  const rows = deployRows(data.connections)
  const [editing, setEditing] = useState<string | null>(null)
  const byId = new Map(data.connections.map((c) => [c.id, c]))
  const appName = new Map(appChoices(data.connections).map((a) => [a.id, a.label]))
  const [latest, setLatest] = useState<LatestDeploys>({ state: 'loading' })
  const hasRows = rows.length > 0

  const load = useCallback(async (isAlive: () => boolean = () => true) => {
    try {
      const r = await listLatestDeploys()
      if (isAlive()) setLatest({ state: 'ok', rows: r })
    } catch (e: unknown) {
      if (isAlive()) setLatest({ state: 'error', reason: e instanceof Error ? e.message : '알 수 없는 오류' })
    }
  }, [])

  // 연결 목록이 바뀌면(저장·끊기 뒤) 다시 읽는다. 연결된 배포 도구가 없으면 부르지 않는다
  useEffect(() => {
    if (!hasRows) return
    let alive = true
    setLatest({ state: 'loading' })
    void load(() => alive)
    return () => { alive = false }
  }, [data, hasRows, load])

  // 주기 새로고침: 첫 로드는 위에서 하므로 immediate: false. 이미 보인 값은 지우지 않고 바꾼다
  useVisibleInterval(() => (hasRows ? load() : undefined), LATEST_POLL_MS, { immediate: false })

  return (
    <section className="space-y-6">
      <SectionHead title="배포"
        desc="인프라 AI가 장애 때 사람이 승인한 롤백을 이 도구로 다시 배포해요. 대상 서비스마다 배포 도구 하나를 이어요." />

      {admin ? (
        <div className="card p-5">
          <ToolForm kind="github_actions" choices={DEPLOY_CHOICES} data={data} admin={admin} title="새 배포 도구" onSaved={onChanged} />
        </div>
      ) : (
        <Notice tone="info">배포 도구는 회사 공용 연결이라 관리자만 바꿀 수 있어요. 지금 연결된 것만 보여요.</Notice>
      )}

      <div className="space-y-3">
        <SectionHead as="h3" title="연결된 배포 도구" />
        {rows.length === 0 ? (
          <p className="border border-dashed border-ink-300 px-6 py-6 text-center text-sm text-ink-500">연결된 배포 도구가 없어요. {admin ? '위에서 도구를 골라 연결해 주세요.' : '관리자가 연결하면 여기에 보여요.'}</p>
        ) : (
          <ul className="space-y-3" aria-label="연결된 배포 도구">
            {rows.map((r) => {
              const conn = byId.get(r.id)
              if (!conn) return null
              const dep = latestDeployView(r.service, latest)
              return (
                <ConnectedTool key={r.id} conn={conn} admin={admin} title={<>{r.service || '대상 서비스 없음'} <span className="font-normal text-ink-500">· {r.tool}</span></>}
                  offNote={`끊으면 인프라 AI가 ${r.service || '이 서비스'}의 롤백을 이 도구로 다시 배포하지 않아요.`}
                  onChanged={() => { setEditing(null); onChanged() }} onEdit={() => setEditing(editing === r.id ? null : r.id)}
                  facts={<>
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" aria-label="최근 배포">
                    <span aria-hidden="true" className={`h-1.5 w-1.5 ${TONE_DOT[dep.tone]}`} />
                    <span className={dep.tone === 'ok' ? 'font-medium text-ink-900' : 'text-ink-500'}>{dep.text}</span>
                    {dep.note && <span className="text-xs text-ink-500">· {dep.note}</span>}
                  </p>
                  <Facts cols={3} items={conn.kind === 'github_actions' ? [
                    ['레포 · 워크플로', <span className="mono">{r.target || '—'}</span>],
                    ['GitHub 앱', appName.get(conn.config.app) ?? '—'],
                    ['버전 입력', <span className="mono">{conn.config.version_input || 'version'}</span>],
                  ] : [
                    ['Jenkins 작업', <span className="mono">{r.target || '—'}</span>],
                    ['주소', <span className="mono">{conn.config.url || '—'}</span>],
                    ['버전 매개변수', <span className="mono">{conn.config.version_param || 'version'}</span>],
                  ]} />
                  </>}>
                  {editing === r.id && (
                    <div className="border-t border-ink-100 pt-4">
                      <ToolForm kind={conn.kind} existing={conn} data={data} admin={admin} title={`${r.service} 배포 도구 고치기`}
                        onSaved={() => { setEditing(null); onChanged() }} onCancel={() => setEditing(null)} />
                    </div>
                  )}
                </ConnectedTool>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
