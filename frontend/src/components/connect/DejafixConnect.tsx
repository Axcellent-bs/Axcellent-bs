import { useCallback, useEffect, useState } from 'react'
import { Facts, Notice, SectionHead, StatusChip } from './parts'
import { ConnectedTool, ToolForm, useToolList } from './ToolForm'
import type { ToolConnection, ToolList } from '@/lib/toolConnections'
import { dejafixStatus } from '@/lib/dejafix'
import { dejafixCard, dejafixSummary, when, type DejafixStatus, type SavedDejafix } from '@/lib/toolForms'

// 회원·서버 관리 › PC·서버 탭 위: DejaFix 연결.
// DejaFix는 AXcellent와 같이 설치된다. 연결 출처는 DejaFix 프록시 `GET /status`가 알려 준다:
// - builtin: 서버 설치 설정(환경 변수)으로 같이 설치된 DejaFix에 연결됨 → 상태와 짧은 설명만. 입력 칸 없음
// - none: 같이 설치된 DejaFix가 설정되지 않음 → 서버 담당 안내
// - custom: 화면에서 저장한 다른 DejaFix(공용 도구 연결 보관함의 dejafix) → 그 연결 + "같이 설치된 DejaFix로 돌아가기"(보관함 연결 지우기)
// - unknown: 상태를 못 읽음 → "확인할 수 없음" (지어내지 않는다)
// 다른 곳에 따로 설치한 DejaFix에 붙이는 입력은 "다른 DejaFix에 연결(고급)" 접힌 영역에만 둔다. 바꾸는 것은 관리자만.
// "현재 장애 n건"은 서버의 마지막 확인 결과 문구에서만 읽는다.

/** 프록시 `/status`. undefined = 받는 중, null = 못 읽음 */
export function useDejafixStatus() {
  const [status, setStatus] = useState<DejafixStatus | null | undefined>(undefined)
  const reload = useCallback(() => { void dejafixStatus().then(setStatus) }, [])
  useEffect(reload, [reload])
  return { status, reload }
}

const OFF_NOTE = '보관함에 저장한 다른 DejaFix 연결을 지워요. 그 뒤로는 AXcellent와 같이 설치된 DejaFix(서버 설치 설정)를 써요. 설치 설정이 없으면 장애 목록을 받지 못해요.'

function SavedRow({ saved, data, admin, onChanged }: { saved: SavedDejafix; data: ToolList; admin: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const done = () => { setEditing(false); onChanged() }
  const conn: ToolConnection = saved.conn
  return (
    <ul>
      <ConnectedTool conn={conn} admin={admin} title="다른 DejaFix" offNote={OFF_NOTE} offLabel="같이 설치된 DejaFix로 돌아가기"
        onChanged={done} onEdit={() => setEditing((v) => !v)}
        facts={<>
          {saved.incidents !== null && (
            <p className="text-lg font-semibold">현재 장애 {saved.incidents.toLocaleString('ko-KR')}건
              <span className="text-sm font-normal text-ink-500"> · {when(saved.checkedAt)} 확인</span></p>
          )}
          <Facts cols={3} items={[
            ['주소', <span className="mono">{conn.config.url ?? '—'}</span>],
            ['계정', <span className="mono">{conn.config.username ?? '—'}</span>],
            ['비밀번호', <span className="mono">{conn.secrets_set.password ?? '없음'}</span>],
          ]} />
        </>}>
        {editing && (
          <div className="border-t border-ink-100 pt-4">
            <ToolForm kind="dejafix" existing={conn} data={data} admin={admin} title="다른 DejaFix 연결 고치기" onSaved={done} onCancel={() => setEditing(false)} />
          </div>
        )}
      </ConnectedTool>
    </ul>
  )
}

/** 따로 설치한 DejaFix에 붙이기 (접힌 영역, 관리자만). 저장된 연결이 남아 있으면 그것을 고치거나 지운다 */
function Advanced({ saved, data, admin, onChanged }: { saved: SavedDejafix | null; data: ToolList; admin: boolean; onChanged: () => void }) {
  if (!admin) return null
  return (
    <details className="group border-t border-ink-100 pt-3 text-sm">
      <summary className="cursor-pointer list-none select-none text-xs font-medium text-ink-500 hover:text-ink-900 [&::-webkit-details-marker]:hidden">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden="true">›</span> 다른 DejaFix에 연결(고급)
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-ink-500">다른 곳에 따로 설치한 DejaFix를 쓸 때만 넣어요. 저장하면 같이 설치된 DejaFix 대신 이 DejaFix에서 장애를 받아요.</p>
        {saved
          ? <SavedRow saved={saved} data={data} admin={admin} onChanged={onChanged} />
          : <ToolForm kind="dejafix" data={data} admin={admin} title="다른 DejaFix 주소·계정" onSaved={onChanged} />}
      </div>
    </details>
  )
}

export function DejafixConnect({ data, status, admin, onChanged }: { data: ToolList; status: DejafixStatus | null; admin: boolean; onChanged: () => void }) {
  const card = dejafixCard(status, data.connections)
  const summary = dejafixSummary(card)

  return (
    <section className="card space-y-4 p-5" aria-label="DejaFix 연결">
      <SectionHead as="h3" title="DejaFix 연결"
        desc="장애 대응 탭과 인프라 AI가 이 DejaFix에서 현재 장애를 받아요."
        right={<StatusChip tone={summary.tone}>{summary.label}</StatusChip>} />
      {card.mode === 'builtin' && <>
        <p className="text-sm text-ink-700">AXcellent와 같이 설치된 DejaFix를 써요. 따로 넣을 주소·계정은 없어요.</p>
        {card.live.tone !== 'ok' && card.detail && <p className="text-sm text-red-700">{card.detail}</p>}
        <Advanced saved={card.saved} data={data} admin={admin} onChanged={onChanged} />
      </>}
      {card.mode === 'none' && <>
        <Notice tone="wait">같이 설치된 DejaFix가 설정되지 않았어요. 서버 담당이 설치 설정에서 연결해요.</Notice>
        <Advanced saved={null} data={data} admin={admin} onChanged={onChanged} />
      </>}
      {card.mode === 'custom' && <>
        <p className="text-sm text-ink-700">화면에서 저장한 다른 DejaFix를 써요{card.live.tone !== 'ok' && card.detail ? ` · 지금 확인: ${card.detail}` : ''}.</p>
        <SavedRow saved={card.saved} data={data} admin={admin} onChanged={onChanged} />
      </>}
      {card.mode === 'unknown' && <>
        <Notice tone="info">DejaFix 연결 상태를 확인할 수 없어요. 잠시 뒤 다시 열어 주세요.</Notice>
        {card.saved && <SavedRow saved={card.saved} data={data} admin={admin} onChanged={onChanged} />}
      </>}
    </section>
  )
}

/** 목록·상태를 직접 받는 카드 (회원·서버 관리에서 쓴다) */
export function DejafixCard({ admin }: { admin: boolean }) {
  const tools = useToolList()
  const live = useDejafixStatus()
  const reload = () => { tools.reload(); live.reload() }
  if (tools.error) return <Notice tone="bad">DejaFix 연결 상태를 불러오지 못했어요: {tools.error}</Notice>
  if (!tools.data || live.status === undefined) return <p className="text-sm text-ink-500">DejaFix 연결을 불러오는 중…</p>
  return <DejafixConnect data={tools.data} status={live.status} admin={admin} onChanged={reload} />
}
