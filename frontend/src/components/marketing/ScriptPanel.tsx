import { useState } from "react";
import {
  DEFAULT_SCRIPT_SECONDS, SCRIPT_LIMITS, SCRIPT_SECONDS, nearestCount, scriptProblem, sourceLabel, spanLabel, updateScene,
  type CreativeScript, type ScriptScene, type ScriptSeconds,
} from "@/lib/creativeScript";
import { saveScript, writeScript } from "@/lib/creativeScriptApi";
import type { ShotCount } from "@/lib/storyboard";

// 마케팅 영상 대본 칸 (11/23). 이미지 시트 → 대본 → 스토리보드 순서의 가운데 단계.
// 만들기 카드·제작 기록 상세에서는 고쳐서 저장하고 [AI로 다시 쓰기]를 할 수 있고,
// AI 대화방 카드에서는 읽기만 한다(editable=false). 바꾼 대본은 onChange로 알린다(스토리보드를 다시 만들 수 있게).

function SceneRow({ s }: { s: ScriptScene }) {
  return (
    <li className="grid gap-1 rounded-xl bg-white p-3 ring-1 ring-ink-100 sm:grid-cols-[3.5rem_1fr]">
      <div className="text-xs text-ink-500">
        <span className="font-semibold tabular-nums text-ink-900">{s.no}</span>
        <span className="block tabular-nums">{spanLabel(s)}</span>
      </div>
      <div className="min-w-0 space-y-1 text-sm">
        <p className="text-ink-900">{s.visual_ko}</p>
        {s.line_ko && <p className="text-ink-700"><span className="text-ink-500">{s.speaker_ko || "내레이션"}:</span> “{s.line_ko}”</p>}
        {s.caption_ko && <p><span className="chip bg-ink-50 text-ink-700">자막 {s.caption_ko}</span></p>}
      </div>
    </li>
  );
}

function Field({ label, value, max, onChange, area }: { label: string; value: string; max: number; onChange: (v: string) => void; area?: boolean }) {
  return (
    <label className="block min-w-0">
      <span className="mb-0.5 block text-xs text-ink-500">{label}</span>
      {area
        ? <textarea rows={2} maxLength={max} value={value} onChange={(e) => onChange(e.target.value)} className="field w-full text-sm" />
        : <input type="text" maxLength={max} value={value} onChange={(e) => onChange(e.target.value)} className="field w-full text-sm" />}
    </label>
  );
}

function SceneEdit({ s, onPatch }: { s: ScriptScene; onPatch: (p: Partial<ScriptScene>) => void }) {
  const time = (key: "start_s" | "end_s", v: string) => onPatch({ [key]: Number(v) });
  return (
    <li className="space-y-2 rounded-xl bg-white p-3 ring-1 ring-ink-100">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <span className="font-semibold tabular-nums text-ink-900">{s.no}번 장면</span>
        <label className="flex items-center gap-1">시작 <input type="number" min={0} max={600} step={0.5} value={s.start_s} onChange={(e) => time("start_s", e.target.value)} className="field w-20 text-xs tabular-nums" />초</label>
        <label className="flex items-center gap-1">끝 <input type="number" min={0.5} max={600} step={0.5} value={s.end_s} onChange={(e) => time("end_s", e.target.value)} className="field w-20 text-xs tabular-nums" />초</label>
      </div>
      <Field label="화면 설명" value={s.visual_ko} max={SCRIPT_LIMITS.visual} onChange={(v) => onPatch({ visual_ko: v })} area />
      <div className="grid gap-2 sm:grid-cols-[8rem_1fr]">
        <Field label="말하는 사람 (비우면 내레이션)" value={s.speaker_ko} max={SCRIPT_LIMITS.speaker} onChange={(v) => onPatch({ speaker_ko: v })} />
        <Field label="대사·내레이션" value={s.line_ko} max={SCRIPT_LIMITS.line} onChange={(v) => onPatch({ line_ko: v })} />
      </div>
      <Field label="자막 (화면 글자)" value={s.caption_ko} max={SCRIPT_LIMITS.caption} onChange={(v) => onPatch({ caption_ko: v })} />
    </li>
  );
}

export function ScriptPanel({ recordId, script, story, scenes, editable = true, onChange }: {
  recordId: string;
  script: CreativeScript;
  /** [AI로 다시 쓰기]에 쓸 줄거리. 비우면 기록의 줄거리 */
  story?: string;
  /** [AI로 다시 쓰기] 장면 수(스토리보드 칸 수) */
  scenes?: ShotCount;
  editable?: boolean;
  onChange?: (s: CreativeScript) => void;
}) {
  const [draft, setDraft] = useState<CreativeScript | null>(null);
  const [seconds, setSeconds] = useState<ScriptSeconds>(
    (SCRIPT_SECONDS as readonly number[]).includes(script.total_seconds) ? (script.total_seconds as ScriptSeconds) : DEFAULT_SCRIPT_SECONDS,
  );
  const [busy, setBusy] = useState<"save" | "write" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const problem = draft ? scriptProblem(draft) : null;

  const run = async (kind: "save" | "write", call: () => Promise<CreativeScript>) => {
    setBusy(kind);
    setError(null);
    try {
      const next = await call();
      setDraft(null);
      onChange?.(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : kind === "save" ? "대본을 저장하지 못했어요" : "대본을 쓰지 못했어요");
    } finally {
      setBusy(null);
    }
  };
  const save = () => draft && !problem && void run("save", () => saveScript(recordId, draft));
  const rewrite = () => void run("write", () => writeScript(recordId, { story, scenes: scenes ?? nearestCount(script.scenes.length), seconds }));

  const shown = draft ?? script;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-base font-semibold text-ink-900">{shown.title_ko}</p>
        <span className="text-xs tabular-nums text-ink-500">{shown.scenes.length}장면 · {script.total_seconds}초</span>
        <span className="chip bg-ink-50 text-ink-700">{sourceLabel(script.source)}</span>
      </div>
      {draft ? (
        <div className="space-y-2">
          <Field label="제목" value={draft.title_ko} max={SCRIPT_LIMITS.title} onChange={(v) => setDraft({ ...draft, title_ko: v })} />
          <ol className="space-y-2" aria-label="대본 장면 고치기">
            {draft.scenes.map((s, i) => <SceneEdit key={s.no} s={s} onPatch={(p) => setDraft(updateScene(draft, i, p))} />)}
          </ol>
          <Field label="끝 문장 (CTA)" value={draft.cta_ko} max={SCRIPT_LIMITS.cta} onChange={(v) => setDraft({ ...draft, cta_ko: v })} />
        </div>
      ) : (
        <>
          <ol className="space-y-2" aria-label="대본 장면">{script.scenes.map((s) => <SceneRow key={s.no} s={s} />)}</ol>
          {script.cta_ko && <p className="text-sm text-ink-900"><span className="text-xs text-ink-500">끝 문장(CTA) </span>{script.cta_ko}</p>}
        </>
      )}
      {script.dropped_ko.length > 0 && !draft && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer">줄거리에 없는 숫자가 있어 뺀 문장 {script.dropped_ko.length}개</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{script.dropped_ko.map((d, i) => <li key={i}>{d}</li>)}</ul>
        </details>
      )}
      {editable && (
        <div className="flex flex-wrap items-center gap-2">
          {draft ? (
            <>
              <button type="button" className="btn-primary text-xs" disabled={busy !== null || problem !== null} onClick={save}>{busy === "save" ? "저장하는 중…" : "고친 대본 저장"}</button>
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={() => { setDraft(null); setError(null); }}>취소</button>
              {problem && <span className="text-xs text-amber-800" role="status">{problem}</span>}
            </>
          ) : (
            <>
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={() => setDraft(script)}>고치기</button>
              <fieldset className="ml-auto flex items-center gap-1" disabled={busy !== null}>
                <legend className="sr-only">대본 길이</legend>
                {SCRIPT_SECONDS.map((n) => (
                  <button key={n} type="button" aria-pressed={seconds === n} onClick={() => setSeconds(n)} className={seconds === n ? "btn-primary text-xs" : "btn-ghost text-xs"}>{n}초</button>
                ))}
              </fieldset>
              <button type="button" className="btn-ghost text-xs" disabled={busy !== null} onClick={rewrite}>{busy === "write" ? "쓰는 중…" : "AI로 다시 쓰기"}</button>
            </>
          )}
        </div>
      )}
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </div>
  );
}
