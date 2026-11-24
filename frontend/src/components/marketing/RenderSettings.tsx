import { useEffect, useState } from "react";
import { renderSettingsApi } from "@/lib/renderSettingsApi";
import {
  CREDITS_STOP_NOTE, PLAN_LABEL, READ_ONLY_NOTE, aspectOptions, changeVideoModel, defaultHint, loadRenderSettings, parseRenderSettings,
  saveBody, saveErrorKo, sameValues, secondsOptions, startValues, videoModelOf,
  type Aspect, type PlanOr, type RenderSettingsState, type RenderValues,
} from "@/lib/renderSettings";

// 계정·연결 › Higgsfield 아래 "만들기 설정": 그림·영상 모델, 그림 크레딧, 영상 길이, 비율 (이 회사에만 적용).
// 서버 API가 없거나 응답이 이상하면 칸 전체를 숨긴다. can_edit이 아니면 보기만(회사 관리자만 바꾼다).
// 비워 둔 칸은 서버 기본값을 쓰고, 그 값을 칸 옆에 회색으로 보여 준다.

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-24 shrink-0 text-xs text-ink-500">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-300">서버 기본값 · {hint}</span>}
    </div>
  );
}

export function RenderSettings() {
  const [state, setState] = useState<RenderSettingsState | null>(null);
  const [vals, setVals] = useState<RenderValues | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const take = (s: RenderSettingsState) => { setState(s); setVals(startValues(s)); };

  useEffect(() => {
    let live = true;
    void loadRenderSettings(() => renderSettingsApi.get()).then((s) => { if (live && s) take(s); });
    return () => { live = false; };
  }, []);

  if (!state || !vals) return null;
  const edit = state.can_edit;
  const model = videoModelOf(state, vals.video_model);
  const secs = secondsOptions(model);
  const aspects = aspectOptions(model);
  const changed = !sameValues(vals, startValues(state));
  const set = (patch: Partial<RenderValues>) => { setVals({ ...vals, ...patch }); setMsg(null); setErr(null); };

  const run = async (job: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const next = parseRenderSettings(await job());
      if (next) take(next);
      else {
        // 응답이 비어 있으면 다시 읽어 맞춘다
        const again = await loadRenderSettings(() => renderSettingsApi.get());
        if (again) take(again);
      }
      setMsg(ok);
    } catch (e) {
      setErr(saveErrorKo(e));
    } finally {
      setBusy(false);
      setAsking(false);
    }
  };

  const segBtn = (on: boolean) => (on ? "btn-primary px-3 py-1.5 text-xs" : "btn-ghost px-3 py-1.5 text-xs");

  return (
    <div className="space-y-3 rounded-xl border border-ink-100 p-4" aria-label="만들기 설정">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold">만들기 설정</h3>
        <span className="text-xs text-ink-500">이 회사에만 적용돼요</span>
      </div>

      <Row label="그림 모델" hint={vals.image_model === null ? defaultHint(state, "image_model") : undefined}>
        <select className="field w-auto text-sm" aria-label="그림 모델" disabled={!edit || busy} value={vals.image_model ?? ""}
          onChange={(e) => set({ image_model: e.target.value || null })}>
          <option value="">정하지 않음</option>
          {state.models.image.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </Row>

      <Row label="그림 크레딧" hint={vals.image_credits_ok === null ? defaultHint(state, "image_credits_ok") : undefined}>
        <button type="button" aria-pressed={vals.image_credits_ok === true} disabled={!edit || busy} className={segBtn(vals.image_credits_ok === true)}
          onClick={() => set({ image_credits_ok: vals.image_credits_ok === true ? null : true })}>쓰기</button>
        <button type="button" aria-pressed={vals.image_credits_ok === false} disabled={!edit || busy} className={segBtn(vals.image_credits_ok === false)}
          onClick={() => set({ image_credits_ok: vals.image_credits_ok === false ? null : false })}>쓰지 않기</button>
      </Row>
      {vals.image_credits_ok === false && <p className="text-xs text-ink-500">{CREDITS_STOP_NOTE}</p>}

      <Row label="영상 모델" hint={vals.video_model === null ? defaultHint(state, "video_model") : undefined}>
        <select className="field w-auto text-sm" aria-label="영상 모델" disabled={!edit || busy} value={vals.video_model ?? ""}
          onChange={(e) => { setVals(changeVideoModel(state, vals, e.target.value || null)); setMsg(null); setErr(null); }}>
          <option value="">정하지 않음</option>
          {state.models.video.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.min_s}~{m.max_s}초</option>)}
        </select>
      </Row>

      <Row label="영상 길이" hint={vals.video_seconds === null ? defaultHint(state, "video_seconds") : undefined}>
        <select className="field w-auto text-sm" aria-label="영상 길이" disabled={!edit || busy} value={vals.video_seconds === null ? "" : String(vals.video_seconds)}
          onChange={(e) => {
            const v = e.target.value;
            set({ video_seconds: v === "" ? null : v === "plan" ? "plan" : (Number(v) as PlanOr<number>) });
          }}>
          <option value="">정하지 않음</option>
          <option value="plan">{PLAN_LABEL}</option>
          {secs.map((n) => <option key={n} value={n}>{n}초</option>)}
        </select>
      </Row>

      <Row label="비율" hint={vals.aspect === null ? defaultHint(state, "aspect") : undefined}>
        <select className="field w-auto text-sm" aria-label="비율" disabled={!edit || busy} value={vals.aspect ?? ""}
          onChange={(e) => set({ aspect: e.target.value === "" ? null : (e.target.value as PlanOr<Aspect>) })}>
          <option value="">정하지 않음</option>
          <option value="plan">{PLAN_LABEL}</option>
          {aspects.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </Row>

      {edit ? (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary text-xs" disabled={busy || !changed}
            onClick={() => void run(() => renderSettingsApi.save(saveBody(vals)), "저장했어요. 다음 영상부터 이 설정으로 만들어요")}>
            {busy ? "저장하는 중…" : "저장"}
          </button>
          {!asking && (
            <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => setAsking(true)}>서버 기본값으로 되돌리기</button>
          )}
          {asking && (
            <span className="flex flex-wrap items-center gap-2 text-xs text-ink-700" role="group" aria-label="되돌리기 확인">
              저장한 설정을 지우고 서버 기본값을 쓸까요?
              <button type="button" className="btn-primary text-xs" disabled={busy}
                onClick={() => void run(() => renderSettingsApi.clear(), "서버 기본값으로 되돌렸어요")}>되돌리기</button>
              <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => setAsking(false)}>취소</button>
            </span>
          )}
        </div>
      ) : (
        <p className="text-xs text-ink-500">{READ_ONLY_NOTE}</p>
      )}
      {msg && <p role="status" className="text-xs text-ink-500">{msg}</p>}
      {err && <p role="alert" className="text-xs text-red-700">{err}</p>}
    </div>
  );
}
