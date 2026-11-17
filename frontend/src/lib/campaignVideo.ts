// 캠페인 시작 화면의 영상 URL(유튜브 쇼츠)·시험 영상 여부 입력. 가벼운 형식 검사만 하고
// 최종 판단은 백엔드가 한다. URL을 넣었을 때만 시험/시연 선택이 필수다(기본값 없음).

export type VideoKind = "test" | "demo";

const YOUTUBE_HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"]);

/** 유튜브 주소처럼 보이는지(https, 유튜브 호스트). 영상 존재 여부는 보지 않는다 */
export function looksLikeYoutubeUrl(raw: string): boolean {
  try {
    const u = new URL(raw.trim());
    return u.protocol === "https:" && YOUTUBE_HOSTS.has(u.hostname.toLowerCase());
  } catch { return false; }
}

/** 칸 입력 → 보낼 영상 필드. 문제가 있으면 error(한국어). URL이 비어 있으면 둘 다 보내지 않는다 */
export function videoFields(url: string, kind: VideoKind | null): { fields: { video_url?: string; test?: boolean }; error: string | null } {
  const v = url.trim();
  if (v === "") return { fields: {}, error: null };
  if (!looksLikeYoutubeUrl(v)) return { fields: {}, error: "유튜브 쇼츠 주소(https://youtube.com/shorts/...)를 넣어 주세요." };
  if (kind === null) return { fields: {}, error: "영상 URL을 넣었으면 시험 영상인지 시연 영상인지 골라 주세요." };
  return { fields: { video_url: v, test: kind === "test" }, error: null };
}
