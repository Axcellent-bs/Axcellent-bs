import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { ToolForm, useToolList } from './ToolForm'
import { deleteTool, type ToolList } from '@/lib/toolConnections'
import { dejafixStatus, saveZabbixSettings, zabbixSettings, zabbixSummary, type ZabbixSettings } from '@/lib/dejafix'
import { when } from '@/lib/toolForms'
import { dejafixCard, dejafixSummary, type DejafixStatus, type SavedDejafix } from '@/lib/toolForms'

// 회원·서버 관리 › DejaFix 연결 탭 위: DejaFix 연결 상태.
// 고객사는 자기 Zabbix·DejaFix를 운영하고, 관리자가 이 화면에서 DejaFix 주소와 조회 계정을 넣어 잇는다(11/25).
// 캣북스처럼 AXcellent와 같이 설치된 DejaFix가 있으면 설치 설정으로 이미 연결돼 있다.
// 연결 출처는 DejaFix 프록시 `GET /status`가 알려 준다(로그인 회사 기준):
// - builtin: 같이 설치된 DejaFix에 연결됨 → 상태 칩 + 관리자용 "Zabbix 연결"(고객사 Zabbix 주소·읽기 전용 토큰,
//   DejaFix 계약 #28·#29, 프록시 `/settings/zabbix`). 고객사는 자기 Zabbix만 알면 된다
// - none: 연결 전 → 관리자에게 연결 폼(주소·조회 계정, 연결 확인 뒤 저장), 관리자가 아니면 안내
// - custom: 이 회사가 화면에서 연결한 DejaFix(보관함) → 응답 여부 + 연결 바꾸기·끊기(관리자만)
// - unknown: 상태를 못 읽음 → "확인할 수 없음" (지어내지 않는다)

/** 프록시 `/status`. undefined = 받는 중, null = 못 읽음 */
export function useDejafixStatus() {
  const [status, setStatus] = useState<DejafixStatus | null | undefined>(undefined)
  const reload = useCallback(() => { void dejafixStatus().then(setStatus) }, [])
  useEffect(reload, [reload])
  return { status, reload }
}

const BACK_NOTE = '이 회사가 연결한 DejaFix 연결을 지워요. 그 뒤로는 장애 목록을 받지 못해요(같이 설치된 DejaFix가 있는 회사는 그걸 써요).'

/** 화면에서 연결한 DejaFix 연결 끊기 (관리자만, 한 번 더 묻는다) */
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
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void remove()}>{busy ? '끊는 중…' : '연결 끊기'}</button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn-ghost" onClick={() => setConfirm(true)}>연결 끊기</button>
      )}
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}
    </div>
  )
}

/** 같이 설치된 DejaFix가 보는 Zabbix (운영자·관리자만). 저장은 DejaFix가 연결을 확인한 뒤에만 된다.
 * 브라우저가 로그인 이메일·비밀번호를 주소·토큰 칸에 채우지 않게 autoComplete를 끈다(11/26 스터디온에서 채워짐) */
function ZabbixConnect() {
  const [now, setNow] = useState<ZabbixSettings | null | undefined>(undefined)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [verify, setVerify] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    let live = true
    zabbixSettings()
      .then((z) => { if (!live) return; setNow(z); setUrl(z.url ?? ''); setVerify(z.verify_tls) })
      .catch((e) => { if (live) { setNow(null); setLoadError((e as Error).message) } })
    return () => { live = false }
  }, [])

  const save = (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null); setDone(false)
    void saveZabbixSettings({ url: url.trim(), token: token.trim(), verify_tls: verify })
      .then((z) => { setNow(z); setToken(''); setDone(true) })
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false))
  }

  if (now === undefined) return <p className="text-sm text-ink-500">Zabbix 연결을 불러오는 중…</p>
  return (
    <div className="space-y-3 border-t border-ink-100 pt-4">
      <div>
        <h4 className="text-sm font-semibold text-ink-900">Zabbix 연결</h4>
        <p className="mt-0.5 text-sm leading-6 text-ink-500">회사에서 쓰는 Zabbix 주소와 읽기 전용 API 토큰을 넣으면, DejaFix가 연결을 확인한 뒤 그 Zabbix의 장애를 받아요. 토큰은 서버에만 저장되고 화면에 다시 보이지 않아요.</p>
      </div>
      {loadError && <Notice tone="bad">Zabbix 연결을 읽지 못했어요: {loadError}</Notice>}
      {now && (
        <p className="text-sm text-ink-700">
          지금: <span className="font-mono">{zabbixSummary(now)}</span>
          {now.updated_at && <span className="text-ink-500"> · {when(now.updated_at)}{now.updated_by ? ` · ${now.updated_by}` : ''}</span>}
          {now.mock && <span className="text-amber-800"> · DejaFix가 목 데이터를 쓰는 중이라 저장해도 실제 Zabbix를 읽지 않아요</span>}
        </p>
      )}
      <form onSubmit={save} className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">Zabbix API 주소</span>
            <input className="field w-full font-mono" name="zabbix-url" type="url" autoComplete="off" placeholder="https://zabbix.example.co.kr/api_jsonrpc.php" value={url} onChange={(e) => setUrl(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">읽기 전용 API 토큰</span>
            <input className="field w-full font-mono" name="zabbix-api-token" type="password" autoComplete="new-password" value={token}
              placeholder={now?.token_set ? '저장된 토큰을 써요. 바꿀 때만 넣어요' : 'Zabbix 사용자 설정 › API 토큰'} onChange={(e) => setToken(e.target.value)} />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-ink-700">
          <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
          HTTPS 인증서 확인 (사내 자체 인증서면 끄세요)
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={busy || !url.trim() || (!token.trim() && !now?.token_set)}>{busy ? '연결 확인하는 중…' : '확인하고 저장'}</button>
        </div>
      </form>
      {done && <p role="status" className="text-sm text-emerald-800">Zabbix 연결을 확인하고 저장했어요. 장애 목록이 곧 새 Zabbix 기준으로 바뀌어요.</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

export function DejafixConnect({ data, status, admin, onChanged }: { data: ToolList; status: DejafixStatus | null; admin: boolean; onChanged: () => void }) {
  const card = dejafixCard(status, data.connections)
  const summary = dejafixSummary(card)
  const [editing, setEditing] = useState(false)
  const saved = card.mode === 'custom' || card.mode === 'unknown' ? card.saved : null
  const form = (title: string) => (
    <ToolForm kind="dejafix" data={data} admin={admin} existing={saved?.conn} title={title}
      onSaved={() => { setEditing(false); onChanged() }} onCancel={saved ? () => setEditing(false) : undefined} />
  )

  return (
    <section className="card space-y-4 p-5" aria-label="DejaFix 연결">
      <SectionHead as="h3" title="DejaFix 연결"
        desc="장애 대응 탭과 인프라 AI가 이 DejaFix에서 현재 장애를 받아요."
        right={<StatusChip tone={summary.tone}>{summary.label}</StatusChip>} />
      {card.mode === 'builtin' && <>
        <p className="text-sm text-ink-700">AXcellent와 같이 설치된 DejaFix를 써요. DejaFix가 볼 회사 Zabbix는 아래에서 정해요.</p>
        {card.live.tone !== 'ok' && card.detail && <p className="text-sm text-red-700">{card.detail}</p>}
        {admin && <ZabbixConnect />}
      </>}
      {card.mode === 'none' && <>
        <p className="text-sm text-ink-700">회사에서 쓰는 DejaFix(Zabbix 장애 대응) 주소와 조회 계정을 넣으면, 연결을 확인한 뒤 저장해요. 계정은 조회(viewer) 권한이면 충분하고 서버에만 저장돼요.</p>
        {admin ? form('DejaFix 연결하기') : <Notice tone="wait">관리자가 DejaFix를 연결하면 장애 목록과 연결한 PC가 보여요.</Notice>}
      </>}
      {card.mode === 'custom' && <>
        <p className="text-sm text-ink-700">
          이 회사가 연결한 DejaFix를 써요 · <span className="font-mono">{card.saved.conn.config.url}</span>
          {card.live.tone !== 'ok' && card.detail ? ` · 지금 확인: ${card.detail}` : ''}
        </p>
        {editing ? form('DejaFix 연결 바꾸기') : admin && (
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>연결 바꾸기</button>
          </div>
        )}
        {!editing && <BackToBuiltin saved={card.saved} admin={admin} onChanged={onChanged} />}
      </>}
      {card.mode === 'unknown' && <>
        <Notice tone="info">DejaFix 연결 상태를 확인할 수 없어요. 잠시 뒤 다시 열어 주세요.</Notice>
        {card.saved && <>
          <p className="text-sm text-ink-700">이 회사가 연결한 DejaFix 연결이 보관함에 있어요.</p>
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
