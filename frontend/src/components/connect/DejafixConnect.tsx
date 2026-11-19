import { useCallback, useEffect, useState } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { useToolList } from './ToolForm'
import { deleteTool, type ToolList } from '@/lib/toolConnections'
import { dejafixStatus } from '@/lib/dejafix'
import { dejafixCard, dejafixSummary, type DejafixStatus, type SavedDejafix } from '@/lib/toolForms'

// 회원·서버 관리 › DejaFix 연결 탭 위: DejaFix 연결 상태.
// DejaFix는 고객사마다 AXcellent와 같이 설치되고, 연결은 설치 설정(환경 변수)으로 정해진다. 화면에서 주소를 넣지 않는다.
// 연결 출처는 DejaFix 프록시 `GET /status`가 알려 준다:
// - builtin: 같이 설치된 DejaFix에 연결됨 → 상태 칩과 짧은 설명만
// - none: 같이 설치된 DejaFix가 설정되지 않음 → 서버 담당 안내
// - custom: 예전에 화면에서 보관함에 저장한 다른 DejaFix → 그 사실 + "같이 설치된 DejaFix로 돌아가기"(보관함 연결 지우기, 관리자만)
// - unknown: 상태를 못 읽음 → "확인할 수 없음" (지어내지 않는다)

/** 프록시 `/status`. undefined = 받는 중, null = 못 읽음 */
export function useDejafixStatus() {
  const [status, setStatus] = useState<DejafixStatus | null | undefined>(undefined)
  const reload = useCallback(() => { void dejafixStatus().then(setStatus) }, [])
  useEffect(reload, [reload])
  return { status, reload }
}

const BACK_NOTE = '보관함에 저장한 다른 DejaFix 연결을 지워요. 그 뒤로는 AXcellent와 같이 설치된 DejaFix(서버 설치 설정)를 써요. 설치 설정이 없으면 장애 목록을 받지 못해요.'

/** 예전에 화면으로 저장한 다른 DejaFix 연결 지우기 (관리자만, 한 번 더 묻는다) */
function BackToBuiltin({ saved, admin, onChanged }: { saved: SavedDejafix; admin: boolean; onChanged: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!admin) return null
  const remove = async () => {
    setBusy(true)
    setError(null)
    try { await deleteTool(saved.conn.id); setConfirm(false); onChanged() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="space-y-2">
      {confirm ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 pt-3">
          <p className="text-xs text-ink-500">{BACK_NOTE}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-quiet" disabled={busy} onClick={() => setConfirm(false)}>취소</button>
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void remove()}>{busy ? '바꾸는 중…' : '같이 설치된 DejaFix로 돌아가기'}</button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn-ghost" onClick={() => setConfirm(true)}>같이 설치된 DejaFix로 돌아가기</button>
      )}
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}
    </div>
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
        <p className="text-sm text-ink-700">AXcellent와 같이 설치된 DejaFix를 써요. 연결은 설치 설정으로 정해져서 화면에서 넣을 것은 없어요.</p>
        {card.live.tone !== 'ok' && card.detail && <p className="text-sm text-red-700">{card.detail}</p>}
      </>}
      {card.mode === 'none' && (
        <Notice tone="wait">같이 설치된 DejaFix가 설정되지 않았어요. 서버 담당이 설치 설정에서 연결해요.</Notice>
      )}
      {card.mode === 'custom' && <>
        <p className="text-sm text-ink-700">예전에 화면에서 저장한 다른 DejaFix를 쓰고 있어요{card.live.tone !== 'ok' && card.detail ? ` · 지금 확인: ${card.detail}` : ''}.</p>
        <BackToBuiltin saved={card.saved} admin={admin} onChanged={onChanged} />
      </>}
      {card.mode === 'unknown' && <>
        <Notice tone="info">DejaFix 연결 상태를 확인할 수 없어요. 잠시 뒤 다시 열어 주세요.</Notice>
        {card.saved && <>
          <p className="text-sm text-ink-700">예전에 화면에서 저장한 다른 DejaFix 연결이 보관함에 남아 있어요.</p>
          <BackToBuiltin saved={card.saved} admin={admin} onChanged={onChanged} />
        </>}
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
