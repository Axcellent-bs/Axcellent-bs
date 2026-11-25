// 마케팅 › SNS·미디어 › 계정·연결 › "만들기 설정" (Higgsfield 그림·영상 모델/길이/비율). 화면 문구·계산을 한곳에 둔다.
// API: GET /api/dashboard/marketing/render-settings · POST .../save · POST .../clear (dashboard-api)
// 서버가 아직 없거나 응답이 이상하면 칸 전체를 숨긴다(지금 화면 그대로). 화면은 값을 지어내지 않는다.

export type Aspect = "9:16" | "1:1" | "16:9";
export const ASPECTS: Aspect[] = ["9:16", "1:1", "16:9"];
/** 값이 "plan"이면 기획서를 따른다 */
export type PlanOr<T> = "plan" | T;

export interface RenderValues {
  image_model: string | null;
  video_model: string | null;
  image_credits_ok: boolean | null;
  video_seconds: PlanOr<number> | null;
  aspect: PlanOr<Aspect> | null;
}
export interface ImageModelOption { id: string; name: string }
export interface VideoModelOption { id: string; name: string; min_s: number; max_s: number; aspects: Aspect[] }
export interface RenderSettingsState {
  settings: RenderValues | null;
  defaults: RenderValues;
  models: { image: ImageModelOption[]; video: VideoModelOption[] };
  can_edit: boolean;
}

export const READ_ONLY_NOTE = "보기만 할 수 있어요. 바꾸는 건 회사 관리자가 해요.";
export const FORBIDDEN_NOTE = "회사 관리자만 바꿀 수 있어요";
export const CREDITS_STOP_NOTE = "그림 단계에서 멈춰요";
export const PLAN_LABEL = "기획서 따르기";

const isObj = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x);
const isAspect = (x: unknown): x is Aspect => x === "9:16" || x === "1:1" || x === "16:9";
const optStr = (x: unknown): string | null | undefined => (x === null || x === undefined ? null : typeof x === "string" ? x : undefined);

function parseValues(x: unknown): RenderValues | null {
  if (!isObj(x)) return null;
  const image_model = optStr(x.image_model);
  const video_model = optStr(x.video_model);
  if (image_model === undefined || video_model === undefined) return null;
  const c = x.image_credits_ok;
  if (c !== null && c !== undefined && typeof c !== "boolean") return null;
  const s = x.video_seconds;
  if (s !== null && s !== undefined && s !== "plan" && !(typeof s === "number" && Number.isInteger(s))) return null;
  const a = x.aspect;
  if (a !== null && a !== undefined && a !== "plan" && !isAspect(a)) return null;
  return {
    image_model, video_model,
    image_credits_ok: c ?? null,
    video_seconds: (s as PlanOr<number> | null | undefined) ?? null,
    aspect: (a as PlanOr<Aspect> | null | undefined) ?? null,
  };
}

/** 서버 응답(`data` 안쪽)을 화면 상태로. 모양이 맞지 않으면 null → 칸을 숨긴다 */
export function parseRenderSettings(raw: unknown): RenderSettingsState | null {
  if (!isObj(raw)) return null;
  const defaults = parseValues(raw.defaults);
  if (!defaults) return null;
  let settings: RenderValues | null = null;
  if (raw.settings !== null && raw.settings !== undefined) {
    settings = parseValues(raw.settings);
    if (!settings) return null;
  }
  const m = raw.models;
  if (!isObj(m) || !Array.isArray(m.image) || !Array.isArray(m.video)) return null;
  const image: ImageModelOption[] = [];
  for (const o of m.image) {
    if (!isObj(o) || typeof o.id !== "string" || typeof o.name !== "string") return null;
    image.push({ id: o.id, name: o.name });
  }
  const video: VideoModelOption[] = [];
  for (const o of m.video) {
    if (!isObj(o) || typeof o.id !== "string" || typeof o.name !== "string" || typeof o.min_s !== "number" || typeof o.max_s !== "number" || !Array.isArray(o.aspects)) return null;
    video.push({ id: o.id, name: o.name, min_s: o.min_s, max_s: o.max_s, aspects: o.aspects.filter(isAspect) });
  }
  if (typeof raw.can_edit !== "boolean") return null;
  return { settings, defaults, models: { image, video }, can_edit: raw.can_edit };
}

/** 불러오기: 실패(404·네트워크·이상한 응답)는 모두 null = 칸 숨김 */
export async function loadRenderSettings(fetcher: () => Promise<unknown>): Promise<RenderSettingsState | null> {
  try {
    return parseRenderSettings(await fetcher());
  } catch {
    return null;
  }
}

/** 화면에 보이는 값(저장값이 없으면 전부 비움) */
export function startValues(s: RenderSettingsState): RenderValues {
  return s.settings ?? { image_model: null, video_model: null, image_credits_ok: null, video_seconds: null, aspect: null };
}

export function videoModelOf(s: RenderSettingsState, id: string | null): VideoModelOption | null {
  return id ? s.models.video.find((m) => m.id === id) ?? null : null;
}

/** 고른 영상 모델의 길이 선택지(기획서 따르기는 늘 따로). 모르는 모델이면 비어 있다 */
export function secondsOptions(model: VideoModelOption | null): number[] {
  if (!model || model.max_s < model.min_s) return [];
  const out: number[] = [];
  for (let n = Math.ceil(model.min_s); n <= model.max_s && out.length < 60; n++) out.push(n);
  return out;
}

/** 고른 영상 모델의 비율 선택지. 모르는 모델이면 셋 모두 */
export function aspectOptions(model: VideoModelOption | null): Aspect[] {
  return model ? model.aspects : ASPECTS;
}

/** 영상 모델을 바꾸면, 새 모델 범위를 벗어난 길이·비율은 "기획서 따르기"로 되돌린다 */
export function changeVideoModel(s: RenderSettingsState, cur: RenderValues, id: string | null): RenderValues {
  const model = videoModelOf(s, id);
  const next: RenderValues = { ...cur, video_model: id };
  if (typeof cur.video_seconds === "number" && !secondsOptions(model).includes(cur.video_seconds)) next.video_seconds = "plan";
  if (cur.aspect !== null && cur.aspect !== "plan" && !aspectOptions(model).includes(cur.aspect)) next.aspect = "plan";
  return next;
}

/** 저장 본문: 5개 키 모두. 비워 둔 칸은 null(서버 기본값을 쓴다) */
export function saveBody(v: RenderValues): RenderValues {
  return {
    image_model: v.image_model || null,
    video_model: v.video_model || null,
    image_credits_ok: v.image_credits_ok,
    video_seconds: v.video_seconds,
    aspect: v.aspect,
  };
}

export function sameValues(a: RenderValues, b: RenderValues): boolean {
  return a.image_model === b.image_model && a.video_model === b.video_model && a.image_credits_ok === b.image_credits_ok
    && a.video_seconds === b.video_seconds && a.aspect === b.aspect;
}

export const secondsLabel = (v: PlanOr<number> | null): string => (v === null ? "" : v === "plan" ? PLAN_LABEL : `${v}초`);
export const aspectLabel = (v: PlanOr<Aspect> | null): string => (v === null ? "" : v === "plan" ? PLAN_LABEL : v);
export const creditsLabel = (v: boolean | null): string => (v === null ? "" : v ? "쓰기" : "쓰지 않기");

/** 칸을 비워 뒀을 때 옆에 회색으로 보이는 서버 기본값 */
export function defaultHint(s: RenderSettingsState, key: keyof RenderValues): string {
  const d = s.defaults[key];
  if (d === null || d === undefined) return "";
  switch (key) {
    case "image_model": return s.models.image.find((m) => m.id === d)?.name ?? String(d);
    case "video_model": return s.models.video.find((m) => m.id === d)?.name ?? String(d);
    case "image_credits_ok": return creditsLabel(d as boolean);
    case "video_seconds": return secondsLabel(d as PlanOr<number>);
    case "aspect": return aspectLabel(d as PlanOr<Aspect>);
  }
}

/** 저장·되돌리기 실패 문구: 403은 고정 문구, 422 등은 서버 문장 그대로 */
export function saveErrorKo(e: unknown): string {
  const status = isObj(e) ? e.status : undefined;
  if (status === 403) return FORBIDDEN_NOTE;
  return e instanceof Error && e.message ? e.message : "저장하지 못했어요";
}
