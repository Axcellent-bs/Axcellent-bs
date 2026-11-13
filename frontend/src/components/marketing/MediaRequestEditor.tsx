import { useState } from "react";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import {
  FIELD_SOURCE_KO, KIND_SOURCE_KO, briefs, handoffReasonKo, mediaFieldKo, mediaKindKo, reviseProblemKo,
  type MediaKindChoice, type MediaRecommendation, type SnsMediaRequest, type StoredBrief,
} from "@/lib/briefs";

// 미디어 요청 초안의 종류·필드. 값은 마케팅 AI가 기획안 글에서 규칙으로 추천해 채운 것이고(서버가 계산),
// 관리자가 여기서 고른 종류·필드가 있으면 그것이 우선한다. 이 화면은 추천을 계산하거나 값을 지어내지 않는다.
// 저장은 요청 초안만 바꾼다: 미디어 생성(비용)·SNS 게시(사람 승인)는 하지 않는다.

const KINDS: MediaKindChoice[] = ["auto", "image", "video", "carousel"];

/** 넘기기 전·후에 공통으로 보여 주는 AI 추천 한 줄 */
export function RecommendationLine({ rec }: { rec: MediaRecommendation | null | undefined }) {
  if (!rec) return <p className="text-xs text-ink-500">마케팅 AI 추천을 읽을 수 없습니다. 종류를 직접 골라 주세요.</p>;
  return (
    <p className="text-xs text-ink-700">
      <span className="chip bg-brand-50 text-brand-700">AI 추천 {mediaKindKo(rec.media_kind)}</span>{" "}
      {rec.confidence === "low" && <span className="chip bg-amber-50 text-amber-800" title="기획안에 뚜렷한 근거가 없어 기본값이거나 추정입니다">확인 권장</span>}{" "}
      <span>근거: {rec.reason}</span>
    </p>
  );
}

export function kindChoiceLabel(kind: MediaKindChoice, rec: MediaRecommendation | null | undefined): string {
  if (kind !== "auto") return mediaKindKo(kind);
  return rec ? `AI 추천 따르기 (${mediaKindKo(rec.media_kind)})` : "AI 추천 따르기";
}

export function KindSelect({ value, onChange, rec, disabled, id }: { value: MediaKindChoice; onChange: (k: MediaKindChoice) => void; rec: MediaRecommendation | null | undefined; disabled?: boolean; id: string }) {
  return (
    <select id={id} className="field py-1 text-xs" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as MediaKindChoice)}>
      {KINDS.map((k) => <option key={k} value={k}>{kindChoiceLabel(k, rec)}</option>)}
    </select>
  );
}

/** 넘긴 뒤: 지금 요청 초안의 종류·필드를 보여 주고 관리자가 바꿔 저장한다 */
export function MediaRequestEditor({ item, request, onDone }: { item: StoredBrief; request: SnsMediaRequest; onDone: () => void }) {
  const rec = item.media_recommendation;
  const legacy = !request.kind_source; // 자동 추천이 들어오기 전에 넘긴 요청
  const [kind, setKind] = useState<MediaKindChoice>(request.kind_source === "admin" ? (request.media_kind as MediaKindChoice) : "auto");
  const [values, setValues] = useState<Record<string, string>>(() => ({ ...(legacy ? rec?.fields ?? {} : request.fields) }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const names = [...new Set([...Object.keys(request.field_options ?? {}), ...Object.keys(request.fields), ...request.missing_fields])];
  const recommended = rec?.fields ?? {};
  const notes = new Map((rec?.field_notes ?? []).map((n) => [n.field, n]));

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // 추천과 다른 값만 관리자 선택으로 보낸다. 같으면 추천을 그대로 따른다
      const fields: Record<string, string> = {};
      for (const name of names) {
        const value = (values[name] ?? "").trim();
        if (value && value !== recommended[name]) fields[name] = value;
      }
      const answer = await briefs.handoff(item.proposal_id, { mediaKind: kind, fields, revise: true });
      const problem = reviseProblemKo(answer.output);
      if (problem) setError(problem);
      else {
        setSaved(true);
        onDone();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg bg-ink-50 px-3 py-3 text-xs" aria-label="미디어 요청 초안">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-ink-700">미디어 요청 초안</p>
        <span className="chip bg-white text-ink-700">{mediaKindKo(request.media_kind)}</span>
        {request.kind_source && <span className="chip bg-white text-ink-700">{KIND_SOURCE_KO[request.kind_source] ?? request.kind_source}</span>}
        {request.missing_fields.length > 0 && <span className="chip bg-amber-50 text-amber-800">필드 미정 {request.missing_fields.length}개</span>}
      </div>
      <RecommendationLine rec={rec} />
      {request.recommendation_blocked && (
        <p className="text-amber-800">추천 종류({mediaKindKo(request.recommended_media_kind)})는 지금 서버에서 만들 수 없어 이미지로 넘겼습니다. {handoffReasonKo(request.recommendation_blocked)}</p>
      )}
      {legacy && <p className="text-ink-500">자동 추천이 생기기 전에 넘긴 요청입니다. 아래 값은 AI 추천이며, 저장하면 요청 초안에 반영됩니다.</p>}

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1" htmlFor={`kind-${item.proposal_id}`}>
          <span className="text-ink-500">미디어 종류</span>
          <KindSelect id={`kind-${item.proposal_id}`} value={kind} onChange={setKind} rec={rec} disabled={busy} />
        </label>
        {names.map((name) => {
          const options = request.field_options?.[name];
          const value = values[name] ?? "";
          const note = notes.get(name);
          const changed = value.trim() !== "" && value.trim() !== recommended[name];
          const source = changed ? "admin" : note ? note.source : request.field_sources?.[name] ?? "undecided";
          const reason = changed ? "저장하면 관리자 선택으로 기록됩니다" : note?.reason ?? request.field_reasons?.[name] ?? "";
          const id = `field-${item.proposal_id}-${name}`;
          return (
            <label key={name} className="space-y-1" htmlFor={id}>
              <span className="text-ink-500">{mediaFieldKo(name)} <span className={`chip ${source === "undecided" ? "bg-amber-50 text-amber-800" : "bg-white text-ink-700"}`}>{FIELD_SOURCE_KO[source] ?? source}</span></span>
              {options ? (
                <select id={id} className="field py-1 text-xs" value={value} disabled={busy} onChange={(e) => setValues({ ...values, [name]: e.target.value })}>
                  <option value="">미정</option>
                  {options.map((o) => <option key={o} value={o}>{o}{o === recommended[name] ? " (추천)" : ""}</option>)}
                </select>
              ) : (
                <input id={id} className="field py-1 text-xs" value={value} disabled={busy} maxLength={request.field_max_len?.[name] ?? 30} placeholder="미정 (짧은 단어)" onChange={(e) => setValues({ ...values, [name]: e.target.value })} />
              )}
              {reason && <span className="block text-ink-500">{reason}</span>}
            </label>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary text-xs" disabled={busy || !HUMAN_TOKEN_SET} onClick={() => void save()} title={HUMAN_TOKEN_SET ? undefined : "사람 토큰이 없어 쓸 수 없습니다"}>{busy ? "저장하는 중" : "종류·필드 저장"}</button>
        <span className="text-ink-500">저장은 요청 초안만 바꿉니다. 미디어 생성(비용)과 게시(사람 승인)는 하지 않습니다.{kind === "carousel" && " 캐러셀 장수는 생성 단계에서 정합니다."}</span>
      </div>
      {saved && !error && <p className="text-brand-700">저장했습니다.</p>}
      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-red-800">{error}</p>}
    </div>
  );
}
