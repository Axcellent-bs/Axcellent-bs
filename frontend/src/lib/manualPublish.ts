// 수동 게시(자동 게시가 꺼진 동안 사람이 Instagram에 직접 올리고 게시물 링크를 기록한다)의 순수 계산.
// `npm test`가 그대로 돈다. 값은 서버(마케팅 `get_manual_publish_package`·`complete_manual_publish`)가 준 것만 쓴다.
// 링크 형식 검사는 서버(`sns_publish/permalink.py`)와 같은 규칙이다. 서버가 최종 판단하고, 여기서는 미리 안내만 한다.
// 이 화면도 서버도 링크가 실제로 있는 게시물인지는 확인하지 못한다(형식·중복만 본다).
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";

const HOSTS = new Set(["instagram.com", "www.instagram.com"]);
const KINDS = new Set(["p", "reel", "tv"]);
const CODE = /^[A-Za-z0-9_-]{5,64}$/;
export const PERMALINK_MAX = 500;

/** 정규화한 링크(`https://www.instagram.com/<p|reel|tv>/<코드>/`). 형식이 맞지 않으면 null */
export function normalizeInstagramPermalink(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text || text.length > PERMALINK_MAX || /\s/.test(text)) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.port || url.username || url.password || !HOSTS.has(url.hostname.toLowerCase())) return null;
  const parts = url.pathname.split("/");
  if (parts[parts.length - 1] === "") parts.pop();
  if (parts.length !== 3 || parts[0] !== "") return null;
  const kind = parts[1].toLowerCase();
  const code = parts[2];
  if (!KINDS.has(kind) || !CODE.test(code)) return null;
  return `https://www.instagram.com/${kind}/${code}/`;
}

/** 입력 칸 아래 안내. 올바르면 null. 비어 있으면 "필수" 안내, 틀렸으면 형식 안내 */
export function permalinkProblemKo(raw: string): string | null {
  if (!raw.trim()) return "게시물 링크를 입력해 주세요. 링크가 있어야 게시 완료로 기록됩니다.";
  if (normalizeInstagramPermalink(raw) === null) {
    return "Instagram 게시물 링크 형식이 아닙니다. 예: https://www.instagram.com/p/코드/ (또는 /reel/코드/, /tv/코드/)";
  }
  return null;
}

/** 링크로 걸어도 되는 값만 돌려준다(서버가 준 값이라도 형식이 맞아야 한다). 아니면 null */
export const safePermalink = (value: unknown): string | null => normalizeInstagramPermalink(value);

/** '게시 완료로 기록' 버튼을 켤지: 링크가 유효하고, AI 표시 안내를 확인했고, 사람 토큰이 있고, 보내는 중이 아니다 */
export function canRecordManual(o: { permalink: string; labelChecked: boolean; tokenSet: boolean; busy: boolean }): boolean {
  return o.tokenSet && o.labelChecked && !o.busy && normalizeInstagramPermalink(o.permalink) !== null;
}

/** 서버가 준 복사용 패키지. 옛 서버는 이 칸이 없다 */
export interface ManualMedia { index: number; asset_ref: string; file_name: string; asset_sha256: string }
export interface ManualPackage {
  post_draft_id: string;
  status: string;
  manual_pending: boolean;
  caption: string;
  hashtags: string[];
  ai_label_text: string;
  ai_label_in_caption: boolean;
  media_kind: string;
  media: ManualMedia[];
  auto_publish_off: boolean;
  pause_reason?: string | null;
  permalink?: string | null;
  posted_by?: string | null;
  published_at?: string | null;
  permalink_verified: boolean;
}

export interface ManualPackageAnswer { status: string; outcome: string | null; output: ManualPackage | null }
export interface ManualCompleteAnswer { status: string; outcome: string | null; output: { permalink?: string; manual?: boolean; posted_by?: string } | null }

export const MANUAL_STEPS: string[] = [
  "아래 캡션을 복사해 Instagram 앱에서 새 게시물을 만듭니다. 캡션과 미디어는 승인된 그대로 올려 주세요.",
  "미디어를 내려받아 올립니다.",
  "AI 생성 표시(\"AI 정보\" 라벨)를 켭니다.",
  "게시한 뒤 게시물 링크를 복사해 아래에 붙여 넣고 '게시 완료로 기록'을 누릅니다.",
];

export const MANUAL_LIMIT_NOTE =
  "링크는 형식과 중복만 확인합니다. 링크가 실제로 있는 게시물인지, 우리 계정의 것인지는 시스템이 확인하지 못하므로 올바른 링크인지 직접 확인해 주세요. 기록한 사람과 시각이 남습니다.";

/** 복사 버튼이 넣을 글: 서버가 준 캡션 전문(해시태그·AI 표시 문구가 이미 들어 있다) */
export const manualCaptionText = (pkg: ManualPackage | null | undefined): string => pkg?.caption ?? "";

const RECORD_FAIL_KO: Record<string, string> = {
  INVALID_PERMALINK: "게시물 링크가 비었거나 형식이 맞지 않아 기록하지 않았습니다.",
  AI_LABEL_NOT_CONFIRMED: "AI 표시를 켰는지 확인해야 기록할 수 있습니다.",
  PERMALINK_ALREADY_USED: "이 링크는 이미 다른 게시 건에 쓰였습니다. 올바른 링크인지 확인해 주세요.",
  ALREADY_PUBLISHED: "이미 게시 완료로 기록된 건입니다. 새로고침해 확인해 주세요.",
  NOT_MANUAL_PENDING: "수동 게시를 기다리는 건이 아니어서 기록하지 않았습니다. 새로고침해 상태를 확인해 주세요.",
  NOT_FOUND: "게시 건을 찾을 수 없습니다.",
  INVALID_INPUT: "입력을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.",
};

/** 완료 기록 호출 오류(HTTP 상태·코드)를 화면 문구로. 서버 문장은 쓰지 않는다 */
export function manualRecordErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (code && RECORD_FAIL_KO[code]) return RECORD_FAIL_KO[code];
  if (status === 401 || status === 403) return "기록할 권한이 없습니다. 로그인이 끝났을 수 있으니 다시 로그인해 주세요(그래도 안 되면 서버의 이 경로 허용을 확인해 주세요).";
  if (status === 404) return "수동 게시 건을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "입력을 서버가 받아들이지 않았습니다. 링크와 AI 표시 확인을 다시 봐 주세요.";
  return "게시 완료를 기록하지 못했습니다. 잠시 뒤 다시 시도해 주세요. 기록되지 않았습니다.";
}

/** 패키지 읽기 오류를 화면 문구로 */
export function manualPackageErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 404) return "수동 게시를 기다리는 건이 아니거나 서버가 아직 이 기능을 지원하지 않습니다.";
  return "수동 게시 안내를 불러오지 못했습니다. 잠시 뒤 새로고침해 주세요.";
}
