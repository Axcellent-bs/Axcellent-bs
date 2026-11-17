import { useState } from 'react'
import { Facts, Notice, SectionHead, StatusChip } from './parts'
import { ConnectedTool, ToolForm, useToolList } from './ToolForm'
import type { ToolList } from '@/lib/toolConnections'
import { dejafixView, when } from '@/lib/toolForms'

// 회원·서버 관리 › PC·서버 탭 위: DejaFix 연결 (공용 도구 연결 보관함의 dejafix, 하나만).
// 장애 대응 탭·인프라 AI·개발 AI가 DejaFix 프록시를 거쳐 이 연결로 장애를 읽는다. 저장하면 재시작 없이 쓴다.
// 안 됨: 주소·계정 칸 + 연결 확인·저장 / 됨: "연결됨 · 현재 장애 n건 · 마지막 확인" + 다시 확인·고치기·끊기. 바꾸는 것은 관리자만.
// "현재 장애 n건"은 서버의 마지막 확인 결과 문구에서만 읽는다 (지어내지 않는다).
export function DejafixConnect({ data, admin, onChanged }: { data: ToolList; admin: boolean; onChanged: () => void }) {
  const view = dejafixView(data.connections)
  const [editing, setEditing] = useState(false)
  const done = () => { setEditing(false); onChanged() }

  return (
    <section className="card space-y-4 p-5" aria-label="DejaFix 연결">
      <SectionHead as="h3" title="DejaFix 연결"
        desc="장애 대응 탭과 인프라 AI가 이 DejaFix에서 현재 장애를 받아요. DejaFix 백엔드 주소와 읽기 전용(viewer) 계정을 넣어요."
        right={view.state === 'none' && <StatusChip tone="off">연결 전</StatusChip>} />
      {view.state === 'connected' ? (
        <ul>
          <ConnectedTool conn={view.conn} admin={admin} title="DejaFix"
            offNote="끊으면 장애 대응 탭과 인프라 AI가 장애 목록을 받지 못해요."
            onChanged={done} onEdit={() => setEditing((v) => !v)}
            facts={<>
              {/* 윗줄(연결됨 · 마지막 확인)에 이어 현재 장애 수. 마지막 확인이 통과했을 때만 */}
              {view.incidents !== null && (
                <p className="text-lg font-semibold">현재 장애 {view.incidents.toLocaleString('ko-KR')}건
                  <span className="text-sm font-normal text-ink-500"> · {when(view.checkedAt)} 확인</span></p>
              )}
              <Facts cols={3} items={[
                ['주소', <span className="mono">{view.conn.config.url ?? '—'}</span>],
                ['계정', <span className="mono">{view.conn.config.username ?? '—'}</span>],
                ['비밀번호', <span className="mono">{view.conn.secrets_set.password ?? '없음'}</span>],
              ]} />
            </>}>
            {editing && (
              <div className="border-t border-ink-100 pt-4">
                <ToolForm kind="dejafix" existing={view.conn} data={data} admin={admin} title="DejaFix 연결 고치기" onSaved={done} onCancel={() => setEditing(false)} />
              </div>
            )}
          </ConnectedTool>
        </ul>
      ) : admin ? (
        <ToolForm kind="dejafix" data={data} admin={admin} onSaved={done} />
      ) : (
        <p className="text-sm text-ink-500">DejaFix가 연결되지 않았어요. 관리자가 연결하면 여기에 보여요.</p>
      )}
    </section>
  )
}

/** 목록을 직접 받는 카드 (회원·서버 관리에서 쓴다) */
export function DejafixCard({ admin }: { admin: boolean }) {
  const tools = useToolList()
  if (tools.error) return <Notice tone="bad">DejaFix 연결 상태를 불러오지 못했어요: {tools.error}</Notice>
  if (!tools.data) return <p className="text-sm text-ink-500">DejaFix 연결을 불러오는 중…</p>
  return <DejafixConnect data={tools.data} admin={admin} onChanged={tools.reload} />
}
