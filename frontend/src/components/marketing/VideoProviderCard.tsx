import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { shortDateTime } from "@/lib/format";
import { approvalLink } from "@/lib/briefMedia";
import { useVisibleInterval } from "@/lib/visibleInterval";
import {
  CUSTOM_SPEC_TEMPLATE, parseCustomSpec, providerOptionLabel, selectionProblem, videoProviderHttpErrorKo, videoProviderResultKo,
  type VideoProviderResult, type VideoProvidersAnswer, type VideoProvidersState,
} from "@/lib/videoProviders";
import { deleteCustomVideoProvider, getVideoProviders, saveCustomVideoProvider, selectVideoProvider } from "@/lib/videoProvidersApi";

// 영상 AI 공급자 선택 + 목록에 없는 API 직접 추가.
// - 목록·현재 선택·키 설정 여부는 서버가 준 값만 보여 준다. 키 값은 서버도 이 화면도 갖지 않고, 환경변수 이름만 다룬다.
// - 고르기·지우기는 바로 적용(A 등급). 직접 추가 저장은 늘 사람 승인(C 등급)이라 보류로 오고, 승인 화면에서 승인한 뒤 같은 저장을 한 번 더 누른다.
// - 직접 추가 스펙은 서버가 저장 전에 검사한다(운영자 허용 호스트·https·키 변수 이름 접두어·가격 필수). 새 공급자는 꺼진 채 저장된다.
// - 바꾸는 사람은 사람 토큰으로만 한다. 이름은 서버가 토큰에서 채운다.

const REFRESH_MS = 20_000;
const TONE = { ok: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-red-800" } as const;
const KIND_TONE: Record<VideoProviderResult["kind"], string> = { done: TONE.ok, held: TONE.warn, failed: TONE.bad };
const HELP_TEXT = JSON.stringify(CUSTOM_SPEC_TEMPLATE, null, 2);

export function VideoProviderCard() {
  const [state, setState] = useState<VideoProvidersState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<VideoProviderResult | null>(null);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [showCustom, setShowCustom] = useState(false);
  const [specText, setSpecText] = useState(HELP_TEXT);
  const [specError, setSpecError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const answer = await getVideoProviders();
      const out = answer.output;
      if (answer.outcome === "state" && out && Array.isArray(out.providers) && out.selected) {
        const next = out as VideoProvidersState;
        setState({ ...next, history: next.history ?? [] });
        if (!dirty) {
          setProvider(next.selected.provider);
          setModel(next.selected.model);
        }
        setError(null);
      } else {
        setState(null);
      }
    } catch (e) {
      setState(null);
      setError(videoProviderHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code));
    } finally {
      setLoaded(true);
    }
  }, [dirty]);
  useVisibleInterval(load, REFRESH_MS);

  const row = useMemo(() => state?.providers.find((p) => p.id === provider), [state, provider]);
  const problem = state ? selectionProblem(row, model) : null;

  async function run(action: () => Promise<VideoProvidersAnswer>) {
    if (busy) return; // 클릭 잠금
    setBusy(true);
    setResult(null);
    setReviewId(null);
    try {
      const answer = await action();
      const shown = videoProviderResultKo(answer);
      setResult(shown);
      if (shown.kind === "held") setReviewId(answer.output?.review_id ?? null);
      if (shown.kind === "done") setDirty(false);
    } catch (e) {
      setResult({ kind: "failed", text: videoProviderHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      setBusy(false);
      void load();
    }
  }

  function pick(id: string) {
    const next = state?.providers.find((p) => p.id === id);
    setProvider(id);
    setModel(next?.models[0] ?? "");
    setDirty(true);
  }

  function saveCustom() {
    const parsed = parseCustomSpec(specText);
    if (!parsed.ok) {
      setSpecError(parsed.message);
      return;
    }
    setSpecError(null);
    void run(() => saveCustomVideoProvider(parsed.spec));
  }

  return (
    <section className="card space-y-3 p-6" aria-label="영상 AI 공급자">
      <div>
        <h2 className="text-lg font-semibold">영상 AI 공급자</h2>
        <p className="mt-1 text-xs text-ink-500">
          영상을 만들 AI와 모델을 고릅니다. 고른 값은 서버 환경변수 설정보다 우선합니다. 서버 모델 표에 없는 모델은 고를 수 없습니다. 원화 단가는 보지 않습니다. 목록에 없는 API는 아래에서 직접 추가할 수 있습니다(사람 승인 필요).
        </p>
      </div>
      {!loaded && <p className="text-sm text-ink-500">불러오는 중</p>}
      {error && <p className="text-xs text-red-700" role="alert">{error}</p>}
      {state && (
        <>
          <p className={`rounded-xl px-3 py-2 text-sm ${TONE.ok}`} role="status">
            현재: <span className="font-semibold">{state.providers.find((p) => p.id === state.selected.provider)?.label ?? state.selected.provider}</span>
            {state.selected.model ? ` · ${state.selected.model}` : ""} · {state.selected.source === "ui" ? `화면에서 선택(${state.selected.updated_by ?? "?"}${state.selected.updated_at ? `, ${shortDateTime(state.selected.updated_at)}` : ""})` : "서버 환경변수 설정"}
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-ink-700">
              공급자
              <select className="field mt-1 block min-w-56" value={provider} onChange={(e) => pick(e.target.value)}>
                {state.providers.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.selectable}>{providerOptionLabel(p)}</option>
                ))}
              </select>
            </label>
            {row && row.id !== "mock" && (
              <label className="text-xs text-ink-700">
                모델
                {row.models.length > 0 ? (
                  <select className="field mt-1 block min-w-48" value={model} onChange={(e) => { setModel(e.target.value); setDirty(true); }}>
                    {row.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                ) : (
                  <span className="mt-1 block rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">지원하는 모델 없음</span>
                )}
              </label>
            )}
            <button className="btn-primary text-xs" disabled={!HUMAN_TOKEN_SET || busy || !dirty || !!problem} onClick={() => void run(() => selectVideoProvider(provider, model))}>
              {busy ? "처리 중" : "이 공급자로 바꾸기"}
            </button>
          </div>
          {row && (
            <p className="text-xs text-ink-500">
              해상도 {row.resolutions.join(", ")} · 길이 {row.min_duration_s}~{row.max_duration_s}초
              {row.key_env && <> · 키 변수 <code>{row.key_env}</code> {row.key_configured ? "설정됨" : "서버에 없음"}</>}
            </p>
          )}
          {dirty && problem && <p className={`rounded-xl px-3 py-2 text-xs ${TONE.warn}`}>{problem}</p>}
          {!HUMAN_TOKEN_SET && <p className="text-xs text-ink-500">로그인이 필요해요. 로그인하면 바꿀 수 있어요.</p>}
        </>
      )}
      {result && (
        <p className={`rounded-xl px-3 py-2 text-sm ${KIND_TONE[result.kind]}`} role="status">
          {result.text}
          {reviewId && <> <Link className="font-medium underline" to={approvalLink(reviewId)}>사람 승인 화면 열기</Link></>}
        </p>
      )}

      <div className="border-t border-ink-100 pt-3">
        <button className="btn-ghost text-xs" onClick={() => setShowCustom((v) => !v)} aria-expanded={showCustom}>
          {showCustom ? "직접 추가 닫기" : "목록에 없는 API 직접 추가"}
        </button>
        {state && !state.custom_host_allowlist_set && (
          <p className="mt-2 text-xs text-amber-800">서버에 허용 호스트(MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS)가 설정되지 않아 직접 추가는 저장되지 않습니다. 설치 운영자가 먼저 등록해야 합니다.</p>
        )}
        {showCustom && (
          <div className="mt-3 space-y-2">
            <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
              <li>제출 → 작업 ID → 상태 조회 → 결과 영상 주소, 이 흐름의 API만 됩니다(공개 주소를 받는 텍스트→영상).</li>
              <li>키는 적지 마세요. <code>secretRef</code>에 서버 환경변수 이름(<code>MARKETING_CUSTOM_</code>로 시작)만 적고, 값은 운영자가 서버에 넣습니다.</li>
              <li>호스트는 운영자가 허용한 https 주소만 됩니다. 가격(<code>pricing</code>)과 길이·해상도 상한은 필수입니다.</li>
              <li>저장은 사람 승인이 필요하고, 새 공급자는 꺼진 채 저장됩니다. 켜려면 <code>enabled</code>를 true로 다시 저장(다시 승인)합니다.</li>
            </ul>
            <label className="block text-xs text-ink-700">
              스펙(JSON)
              <textarea className="field mt-1 h-80 w-full font-mono text-xs" spellCheck={false} value={specText} onChange={(e) => { setSpecText(e.target.value); setSpecError(null); }} />
            </label>
            {specError && <p className="text-xs text-red-700" role="alert">{specError}</p>}
            <div className="flex gap-2">
              <button className="btn-primary text-xs" disabled={!HUMAN_TOKEN_SET || busy} onClick={saveCustom}>{busy ? "처리 중" : "저장(사람 승인 필요)"}</button>
              <button className="btn-ghost text-xs" onClick={() => { setSpecText(HELP_TEXT); setSpecError(null); }}>예시로 되돌리기</button>
            </div>
          </div>
        )}
        {state && state.providers.some((p) => p.kind === "custom") && (
          <ul className="mt-3 space-y-1 text-xs text-ink-700">
            {state.providers.filter((p) => p.kind === "custom").map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{p.label}</span>
                <span className="chip">{p.enabled ? "켜짐" : "꺼짐"}</span>
                <span className="text-ink-500">{p.id}</span>
                <button className="btn-ghost text-xs" disabled={!HUMAN_TOKEN_SET || busy} onClick={() => setSpecText(JSON.stringify(p.spec, null, 2))}>스펙 불러오기</button>
                <button className="btn-ghost text-xs" disabled={!HUMAN_TOKEN_SET || busy} onClick={() => void run(() => deleteCustomVideoProvider(p.id.replace(/^custom:/, "")))}>지우기</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {state && state.history.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer">최근 변경 내력 {state.history.length}건</summary>
          <ul className="mt-2 space-y-1">
            {state.history.map((h, i) => (
              <li key={`${h.at}-${i}`}>{shortDateTime(h.at)} · {h.by} · {h.action} · {h.target}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
