// 영상 AI 공급자 선택·커스텀 등록(마케팅 `list/select/save_custom/delete_custom_video_provider`, dashboard-api 중계).
// 순수 계산은 `npm test`가 그대로 돈다. 값은 서버가 준 것만 쓴다. 키 값은 서버도 화면도 갖지 않는다(환경변수 이름만).
// 네트워크 호출은 `videoProvidersApi.ts`(순수 계산만 테스트에서 돈다). 선택·삭제는 바로 적용(A 등급), 커스텀 저장은 늘 사람 승인(C 등급)이라 보류(held)로 온다.

export interface VideoProviderRow {
  id: string;
  label: string;
  kind: "builtin" | "custom";
  key_env: string | null;
  key_configured: boolean;
  resolutions: string[];
  min_duration_s: number;
  max_duration_s: number;
  /** 서버 모델 표(길이·해상도 능력표)에 있는 영상 모델. 비어 있으면 아직 지원 모델이 없다. 원화 단가는 보지 않는다 */
  models: string[];
  priced: boolean;
  selectable: boolean;
  enabled?: boolean;
  spec?: Record<string, unknown>;
}

export interface VideoProvidersState {
  selected: { provider: string; model: string; source: "ui" | "env"; updated_by: string | null; updated_at: string | null };
  providers: VideoProviderRow[];
  custom_host_allowlist_set: boolean;
  history: { at: string; by: string; action: string; target: string }[];
}

export interface VideoProvidersAnswer {
  status: string;
  outcome: string | null;
  output: (Partial<VideoProvidersState> & { review_id?: string; reason?: string }) | null;
}

export type VideoProviderResult = { kind: "done" | "held" | "failed"; text: string };

const FAIL_KO: Record<string, string> = {
  invalid_spec: "스펙이 규칙에 맞지 않습니다",
  invalid_input: "입력이 맞지 않습니다",
  unknown_provider: "목록에 없는 공급자입니다",
  provider_disabled: "꺼져 있는 공급자는 고를 수 없습니다. 사람 승인으로 켠 뒤 고르세요",
  model_not_priced: "서버 모델 표에 있는 모델만 고를 수 있습니다",
  not_found: "이미 없는 공급자입니다",
  too_many_custom_providers: "커스텀 공급자는 20개까지입니다",
  video_store_error: "서버가 설정을 저장하지 못했습니다",
};

const DONE_KO: Record<string, string> = {
  selected: "영상 공급자를 바꿨습니다. 다음 영상 생성부터 적용됩니다.",
  saved: "커스텀 공급자를 저장했습니다.",
  deleted: "커스텀 공급자를 지웠습니다.",
};

/** 응답을 화면 문구로. 보류(held)면 승인 방법을 알린다. 서버가 준 사유(reason)는 스펙 검사 사유뿐이다 */
export function videoProviderResultKo(answer: VideoProvidersAnswer): VideoProviderResult {
  if (answer.status === "input-required" || answer.outcome === "held") {
    return { kind: "held", text: "사람 승인이 필요합니다. 승인 화면에서 승인한 뒤 같은 저장 버튼을 한 번 더 누르면 적용됩니다." };
  }
  if (answer.outcome && DONE_KO[answer.outcome]) return { kind: "done", text: DONE_KO[answer.outcome] };
  const base = (answer.outcome && FAIL_KO[answer.outcome]) || `처리하지 못했습니다${answer.outcome ? ` (${answer.outcome})` : ""}`;
  const reason = answer.outcome === "invalid_spec" && typeof answer.output?.reason === "string" ? `: ${answer.output.reason}` : "";
  return { kind: "failed", text: `${base}${reason}` };
}

export function videoProviderHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (status === 401 || status === 403) return "바꿀 권한이 없습니다. 로그인이 끝났을 수 있으니 다시 로그인해 주세요.";
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return code === "INVALID_SPEC" ? "스펙이 규칙에 맞지 않습니다." : "입력을 서버가 받아들이지 않았습니다.";
  if (status === 409) return "지금 상태에서는 할 수 없습니다(꺼진 공급자이거나 모델 표에 없는 모델).";
  return "영상 공급자 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

/** 드롭다운 항목 한 줄. 막힌 사유를 같이 적는다(고를 수 없음·지원 모델 없음·키 없음) */
export function providerOptionLabel(row: VideoProviderRow): string {
  const notes: string[] = [];
  if (row.kind === "custom") notes.push("직접 추가");
  if (!row.selectable) notes.push("꺼짐");
  if (row.id !== "mock" && !row.priced) notes.push("지원 모델 없음");
  if (row.key_env && !row.key_configured) notes.push("키 없음");
  return notes.length ? `${row.label} (${notes.join(", ")})` : row.label;
}

/** 지금 고른 조합이 호출까지 갈 수 있는지 한 줄 */
export function selectionProblem(row: VideoProviderRow | undefined, model: string): string | null {
  if (!row) return "목록에 없는 공급자입니다.";
  if (!row.selectable) return "꺼져 있는 공급자입니다.";
  if (row.id === "mock") return null;
  if (row.kind === "builtin" && !row.priced) return "서버 모델 표에 지원하는 모델이 없어 영상을 만들 수 없습니다.";
  if (row.kind === "builtin" && !row.models.includes(model)) return "서버 모델 표에 있는 모델을 고르세요.";
  if (row.key_env && !row.key_configured) return `키 환경변수 ${row.key_env}가 서버에 없어 호출이 실패합니다.`;
  return null;
}

/** 직접 추가 양식의 시작 예시(비동기 작업 큐 공급자 공통 모양). 값은 모두 자리 표시다 */
export const CUSTOM_SPEC_TEMPLATE = {
  id: "my-video",
  label: "내 영상 API",
  enabled: false,
  baseUrl: "https://api.example.com",
  defaultModel: "model-name",
  auth: { type: "header", headerName: "Authorization", valueTemplate: "Bearer {{secret}}", secretRef: "MARKETING_CUSTOM_MY_VIDEO_KEY" },
  submit: {
    method: "POST",
    path: "/v1/videos",
    bodyTemplate: { model: "{{model}}", prompt: "{{prompt}}", duration: "{{durationSec}}", resolution: "{{resolution}}", aspect_ratio: "{{aspectRatio}}" },
    taskIdPath: "data.id",
  },
  poll: { method: "GET", path: "/v1/videos/{{taskId}}", intervalSec: 5, timeoutSec: 600, statusPath: "data.status", successValues: ["succeeded"], failureValues: ["failed"], errorPath: "data.error.code" },
  result: { videoUrlPath: "data.output[0].url", urlNeedsAuth: false, allowedHosts: [] },
  limits: { resolutions: ["720p"], minDurationSec: 3, maxDurationSec: 8 },
  pricing: { perSecondUsd: 0.1, source: "공급자 가격 페이지 주소와 확인한 날짜" },
};

export type SpecParse = { ok: true; spec: Record<string, unknown> } | { ok: false; message: string };

/** 붙여 넣은 JSON을 검사한다(모양만. 규칙 검사는 서버가 한다). 키처럼 보이는 값은 막는다 */
export function parseCustomSpec(text: string): SpecParse {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { ok: false, message: "JSON 형식이 맞지 않습니다." };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return { ok: false, message: "맨 위가 객체여야 합니다." };
  if (/sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9._-]{20,}/.test(text)) {
    return { ok: false, message: "키 값처럼 보이는 글자가 있습니다. 키는 환경변수 이름(secretRef)으로만 적습니다." };
  }
  return { ok: true, spec: value as Record<string, unknown> };
}
