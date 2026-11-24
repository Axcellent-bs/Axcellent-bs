import { useEffect, useMemo, useState } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { instagramRadar, splitAccounts, type RadarState } from '@/lib/instagramRadar'
import { RADAR_SOURCE as SOURCE } from '@/lib/radarSource'
import {
  AGE_LABEL, AGE_ORDER, KIND_LABEL, KIND_ORDER, META_NOTE, TOPIC_MAX, buildRadarRows, draftBody, draftOf, layerCounts, layerCountsLine,
  tagSourceView, thinNote, toggleAge, ageLabels, genderLabels, type MetaDraft, type RadarRow,
} from '@/lib/radarMeta'
import type { AudienceGender, RadarKind } from '@/lib/data'

// 인스타 레이더 계정: 데이터 AI가 반응을 지켜볼 인스타 계정(업계·경쟁·참고 계정). 데이터분석 › 트렌드 › 인스타의 레이더 아래에서 정한다.
// 저장하면 회사 설정 파일(trends.toml)의 목록 대신 이 목록을 쓴다. "회사 설정 파일로 되돌리기"로 저장 목록을 지운다.
// 둘 다 없으면 경쟁사 찾기로 확인한 계정(자동)을 칸에 보여 주고 레이더도 그 목록을 쓴다.
// 표: 계정 · 팔로워 · 종류 · 주로 보는 층 · 태그 출처. 태그가 없는 계정은 목록 소속으로 종류만 정하고 층은 비운다. [고치기]는 사람 토큰으로만.
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

const TONE = { ok: 'bg-emerald-50 text-emerald-700', warn: 'bg-amber-50 text-amber-700', muted: 'bg-ink-100 text-ink-500' } as const
const GENDERS: { id: AudienceGender | ''; label: string }[] = [
  { id: '', label: '모름' }, { id: 'male', label: '남성' }, { id: 'female', label: '여성' }, { id: 'all', label: '남녀' },
]

function Editor({ row, busy, canWrite, onSave, onCancel }: {
  row: RadarRow; busy: boolean; canWrite: boolean; onSave: (d: MetaDraft) => void; onCancel: () => void
}) {
  const [d, setD] = useState<MetaDraft>(() => draftOf(row))
  return (
    <div className="space-y-2 bg-ink-50 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5">
          <span className="text-ink-700">종류</span>
          <select className="field" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as RadarKind })}>
            {KIND_ORDER.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-ink-700">성별</span>
          <select className="field" value={d.gender} onChange={(e) => setD({ ...d, gender: e.target.value as AudienceGender | '' })}>
            {GENDERS.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-ink-700">주제</span>
          <input className="field w-40" value={d.topic} maxLength={TOPIC_MAX} placeholder="예: 뷰티·패션"
            onChange={(e) => setD({ ...d, topic: e.target.value })} />
        </label>
      </div>
      <fieldset className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <legend className="sr-only">주로 보는 나이대</legend>
        <span className="text-ink-700">나이대</span>
        {AGE_ORDER.map((a) => (
          <label key={a} className="flex items-center gap-1">
            <input type="checkbox" checked={d.ages.includes(a)} onChange={() => setD({ ...d, ages: toggleAge(d.ages, a) })} />
            {AGE_LABEL[a]}
          </label>
        ))}
      </fieldset>
      <div className="flex gap-2">
        <button type="button" className="btn-primary" disabled={busy || !canWrite} onClick={() => onSave(d)}>{busy ? '저장 중…' : '저장'}</button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>닫기</button>
      </div>
    </div>
  )
}

function Chips({ row }: { row: RadarRow }) {
  const labels = [...ageLabels(row.ages), ...genderLabels(row.genders)]
  if (labels.length === 0) return <span className="text-ink-400">—</span>
  return <span className="flex flex-wrap gap-1">{labels.map((l) => <span key={l} className="chip text-[11px]">{l}</span>)}</span>
}

export default function InstagramRadarSetup() {
  const [state, setState] = useState<RadarState | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)

  const [memeText, setMemeText] = useState('')
  const take = (s: RadarState) => { setState(s); setText(s.accounts.join('\n')); setMemeText((s.meme_accounts ?? []).join('\n')) }
  useEffect(() => {
    let live = true
    instagramRadar.get().then((s) => { if (live) take(s) }).catch((e) => { if (live) setError(message(e)) })
    return () => { live = false }
  }, [])

  const run = async (work: () => Promise<RadarState>, ok: string) => {
    setBusy(true); setError(null); setDone(null)
    try { take(await work()); setDone(ok); return true } catch (e) { setError(message(e)); return false } finally { setBusy(false) }
  }
  const typed = splitAccounts(text)
  const typedMeme = splitAccounts(memeText)
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)
  const canEditMeta = HUMAN_TOKEN_SET && (state?.meta_editable ?? false)
  const max = state?.max ?? 200
  const savedMeme = state?.meme_accounts?.length ?? 0
  const canClear = state !== null && ((state.source === 'screen' && state.tenant_count > 0) || (state.meme_source === 'screen' && (state.meme_tenant_count ?? 0) > 0))
  const status = !state ? null : state.accounts.length + savedMeme
    ? <StatusChip tone="ok">{state.accounts.length + savedMeme}곳 · {SOURCE[state.source]}</StatusChip>
    : <StatusChip tone="wait">계정 없음</StatusChip>
  const rows = useMemo(() => buildRadarRows(state), [state])
  const counts = useMemo(() => layerCounts(rows), [rows])
  const thin = thinNote(counts, rows)
  const saveMeta = async (row: RadarRow, d: MetaDraft) => {
    const ok = await run(() => instagramRadar.saveMeta(draftBody(row.account, d)), `${row.account} 태그를 저장했어요. "사람 확인"으로 바뀌었어요.`)
    if (ok) setEditing(null)
  }

  return (
    <section className="card space-y-4 p-6" aria-label="인스타 레이더 계정">
      <SectionHead as="h3" title="인스타 레이더 계정" right={status}
        desc="위 레이더가 반응을 지켜볼 인스타 계정이에요(업계·경쟁·참고 계정). 계정마다 주로 보는 층을 달아 두면 보고서가 대상층에 맞는 계정부터 봐요." />
      {!HUMAN_TOKEN_SET && <Notice>로그인이 필요해요. 로그인하면 저장할 수 있어요.</Notice>}
      {state && !state.editable && <Notice>서버에 회사 연결 파일 설정이 없어 저장할 수 없어요.</Notice>}
      {state && !state.meta_editable && HUMAN_TOKEN_SET && <Notice>서버에서 태그를 고칠 수 없는 상태예요.</Notice>}

      <p className="text-xs text-ink-500">{META_NOTE}</p>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-500">{state ? '레이더 계정이 없어요. 아래에 계정을 넣고 저장해요.' : '받는 중…'}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
                <th className="whitespace-nowrap px-1 py-1.5 font-medium">계정</th>
                <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">팔로워</th>
                <th className="whitespace-nowrap px-1 py-1.5 font-medium">종류</th>
                <th className="whitespace-nowrap px-1 py-1.5 font-medium">주로 보는 층</th>
                <th className="whitespace-nowrap px-1 py-1.5 font-medium">태그 출처</th>
                <th className="px-1 py-1.5"><span className="sr-only">고치기</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const src = tagSourceView(r.tagSource)
                return (
                  <tr key={r.account} className={`border-b border-ink-100 align-top ${r.excluded ? 'text-ink-500' : ''}`}>
                    <td className="min-w-[8rem] px-1 py-2">
                      <b className={r.excluded ? 'font-medium' : 'text-ink-900'}>@{r.account}</b>
                      {r.excluded && <small className="block text-[11px] text-ink-500">{r.excluded}</small>}
                      {editing === r.account && (
                        <div className="mt-2">
                          <Editor row={r} busy={busy} canWrite={canEditMeta} onSave={(d) => void saveMeta(r, d)} onCancel={() => setEditing(null)} />
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">{r.followersLabel}</td>
                    <td className="px-1 py-2">
                      <span className="flex flex-wrap gap-1">
                        <span className="chip text-[11px]">{r.kindLabel}</span>
                        {r.topic && <span className="chip text-[11px]">{r.topic}</span>}
                      </span>
                    </td>
                    <td className="px-1 py-2"><Chips row={r} /></td>
                    <td className="px-1 py-2"><span className={`chip text-[11px] ${TONE[src.tone]}`}>{src.label}</span></td>
                    <td className="whitespace-nowrap px-1 py-2 text-right">
                      <button type="button" className="btn-ghost" disabled={!canEditMeta || busy}
                        onClick={() => setEditing(editing === r.account ? null : r.account)}>고치기</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs text-ink-700">층별 계정 수: {layerCountsLine(counts)}</p>
          {thin && <span className={`chip text-[11px] ${TONE.warn}`}>{thin}</span>}
        </div>
      )}

      <details className="space-y-3">
        <summary className="cursor-pointer select-none text-sm font-medium text-ink-700">여러 줄 붙여 넣기</summary>
        <label className="mt-3 block space-y-1 text-sm">
          <span className="text-ink-700">벤치마크 계정 (@는 빼도 돼요)</span>
          <textarea className="field h-48 w-full font-mono text-xs" value={text} onChange={(e) => { setText(e.target.value); setDone(null) }}
            placeholder={'our_brand_official\nproduct_reviewer'} />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-ink-700">밈·크리에이터 계정 (한 줄에 하나)</span>
          <textarea className="field h-32 w-full font-mono text-xs" value={memeText} onChange={(e) => { setMemeText(e.target.value); setDone(null) }}
            placeholder={'meme_creator_a\nmeme_creator_b'} />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary" disabled={busy || !canWrite}
            onClick={() => void run(() => instagramRadar.save(typed, typedMeme), `벤치마크 ${typed.length}곳 + 밈 ${typedMeme.length}곳을 저장했어요. 데이터 AI 레이더가 다음 회차부터 이 목록을 써요.`)}>
            {busy ? '저장 중…' : '저장'}
          </button>
          {canClear && state && (
            <button type="button" className="btn-ghost" disabled={busy || !canWrite}
              onClick={() => void run(() => instagramRadar.clear(), '회사 설정 파일 목록으로 되돌렸어요.')}>
              회사 설정 파일로 되돌리기 ({state.tenant_count}곳{(state.meme_tenant_count ?? 0) > 0 ? ` + 밈 ${state.meme_tenant_count}곳` : ''})
            </button>
          )}
          <span className="text-xs text-ink-500">벤치마크 {typed.length}곳 + 밈 {typedMeme.length}곳 / {max}</span>
        </div>
      </details>
      <More summary="어떻게 쓰이나요">
        <p>밈·크리에이터 계정은 갑자기 뜬 게시물에서 따로 보고, 팔로워 대비 반응도 같이 봐요.</p>
        <p>밈·크리에이터 계정은 팔로워가 {state?.min_followers ? `${(state.min_followers / 10_000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}만` : '5만'} 미만이면 뜬 게시물에서 빼요.</p>
        <p>저장하면 회사 설정 파일(trends.toml)에 적힌 목록 대신 이 목록을 써요. 한 번에 많이 부르지 않게 묶음으로 나눠 읽어요.</p>
        <p>둘 다 없으면 분석 보고서의 경쟁사 찾기가 공식 API로 확인한 계정을 자동으로 써요.</p>
        <p>인스타 API 키(데이터용 계정)는 서버 설정에 있어야 실제로 읽혀요.</p>
      </More>
      {done && <p role="status" className="text-sm text-emerald-800">{done}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  )
}
