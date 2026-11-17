import { useState } from 'react'
import { Facts, SectionHead, StatusChip } from './parts'
import { ConnectedTool, ToolForm } from './ToolForm'
import type { ToolList } from '@/lib/toolConnections'

// 도구 연결 › 형상관리 맨 위: 공용 보관함의 GitHub 앱(github_app). 배포(GitHub Actions)가 이 앱으로 워크플로를 띄운다.
// 아래 레포 연결(개발 AI 저장소)과는 아직 따로다. 레포 연결이 이 앱을 고르게 되는 것은 다음 단계(도구 연결 설계 PR 6).
// 개인 키는 파일에서 읽어 보내기만 하고, 저장 뒤에는 "설정됨"만 보인다.
export function GithubAppCard({ data, admin, onChanged }: { data: ToolList; admin: boolean; onChanged: () => void }) {
  const apps = data.connections.filter((c) => c.kind === 'github_app')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const users = (id: string) => data.connections.filter((c) => c.kind === 'github_actions' && c.config.app === id)
  const done = () => { setAdding(false); setEditing(null); onChanged() }

  return (
    <section className="card space-y-4 p-5" aria-label="GitHub 앱">
      <SectionHead as="h3" title="GitHub 앱"
        desc="배포(GitHub Actions)가 이 앱 권한으로 워크플로를 띄워요. 조직에 GitHub 앱을 만들어 설치한 뒤 앱 ID·설치 ID·개인 키를 넣어요."
        right={<>
          {apps.length ? <StatusChip tone="ok">{apps.length}개 연결</StatusChip> : <StatusChip tone="off">연결 전</StatusChip>}
          {admin && apps.length > 0 && !adding && <button type="button" className="btn-ghost" onClick={() => setAdding(true)}>앱 하나 더</button>}
        </>} />

      {apps.length > 0 && (
        <ul className="space-y-3" aria-label="연결된 GitHub 앱">
          {apps.map((c) => {
            const used = users(c.id)
            return (
              <ConnectedTool key={c.id} conn={c} admin={admin} title={c.name || `GitHub 앱 ${c.config.app_id ?? ''}`}
                offNote={used.length ? `배포 ${used.map((u) => u.name).join(', ')}이(가) 이 앱을 써요. 그 배포 도구를 먼저 끊어야 해요.` : '끊으면 이 앱으로 띄우는 배포가 멈춰요.'}
                onChanged={done} onEdit={() => setEditing(editing === c.id ? null : c.id)}
                facts={<Facts cols={4} items={[
                  ['앱 ID', <span className="mono">{c.config.app_id ?? '—'}</span>],
                  ['설치 ID', <span className="mono">{c.config.installation_id ?? '—'}</span>],
                  ['개인 키', c.secrets_set.private_key ?? '없음'],
                  ['주소', <span className="mono">{c.config.base_url || 'api.github.com'}</span>],
                ]} />}>
                {editing === c.id && (
                  <div className="border-t border-ink-100 pt-4">
                    <ToolForm kind="github_app" existing={c} data={data} admin={admin} title="GitHub 앱 고치기" onSaved={done} onCancel={() => setEditing(null)} />
                  </div>
                )}
              </ConnectedTool>
            )
          })}
        </ul>
      )}

      {admin && (apps.length === 0 || adding) && (
        <div className={apps.length ? 'border-t border-ink-100 pt-4' : ''}>
          <ToolForm kind="github_app" data={data} admin={admin} title={apps.length ? '새 GitHub 앱' : undefined} onSaved={done} onCancel={apps.length ? () => setAdding(false) : undefined} />
        </div>
      )}
      {!admin && apps.length === 0 && <p className="text-sm text-ink-500">연결된 GitHub 앱이 없어요. 관리자가 연결하면 여기에 보여요.</p>}
    </section>
  )
}
