import { useState } from 'react'
import { Facts, Notice, SectionHead } from './parts'
import { ConnectedTool, ToolForm } from './ToolForm'
import type { ToolList } from '@/lib/toolConnections'
import { DEPLOY_CHOICES, appChoices, deployRows } from '@/lib/toolForms'

// 도구 연결 › 배포: 인프라 AI가 장애 때 승인된 롤백을 다시 배포하는 도구(GitHub Actions·Jenkins).
// 맨 위 새 연결 하나(도구 고르기 → 그 도구 칸 → 연결 확인 → 저장), 아래는 연결된 배포 도구만.
// 한 서비스에 배포 도구는 하나 (두 번째는 서버가 409로 막는다). 관리자만 바꾸고 일반 회원은 상태만 본다.
export function DeployTools({ data, admin, onChanged }: { data: ToolList; admin: boolean; onChanged: () => void }) {
  const rows = deployRows(data.connections)
  const [editing, setEditing] = useState<string | null>(null)
  const byId = new Map(data.connections.map((c) => [c.id, c]))
  const appName = new Map(appChoices(data.connections).map((a) => [a.id, a.label]))

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
              return (
                <ConnectedTool key={r.id} conn={conn} admin={admin} title={<>{r.service || '대상 서비스 없음'} <span className="font-normal text-ink-500">· {r.tool}</span></>}
                  offNote={`끊으면 인프라 AI가 ${r.service || '이 서비스'}의 롤백을 이 도구로 다시 배포하지 않아요.`}
                  onChanged={() => { setEditing(null); onChanged() }} onEdit={() => setEditing(editing === r.id ? null : r.id)}
                  facts={<Facts cols={3} items={conn.kind === 'github_actions' ? [
                    ['레포 · 워크플로', <span className="mono">{r.target || '—'}</span>],
                    ['GitHub 앱', appName.get(conn.config.app) ?? '—'],
                    ['버전 입력', <span className="mono">{conn.config.version_input || 'version'}</span>],
                  ] : [
                    ['Jenkins 작업', <span className="mono">{r.target || '—'}</span>],
                    ['주소', <span className="mono">{conn.config.url || '—'}</span>],
                    ['버전 매개변수', <span className="mono">{conn.config.version_param || 'version'}</span>],
                  ]} />}>
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
