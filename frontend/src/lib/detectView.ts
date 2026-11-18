// 트렌드 1 감지 하위 탭의 플랫폼 칩. 주소 #detect?view=all|youtube 로 남긴다.
// - 칩은 배열 한 곳. 인스타·검색은 뒤에서 이 배열에 더한다(이번에는 없음)
// - view가 없고 영상 탐색 조건(len·p·sort·cat)만 있는 예전 주소는 유튜브 칩으로 연다
// - 영상 탐색(VideoExplorer)의 조건 파라미터는 그대로 두고 view만 더하거나 바꾼다

export type DetectView = "all" | "youtube";

export const DETECT_VIEWS: readonly { key: DetectView; label: string }[] = [
  { key: "all", label: "종합" },
  { key: "youtube", label: "유튜브" },
];

const EXPLORER_PARAMS = ["len", "p", "sort", "cat"];

function paramsOf(hash: string): URLSearchParams {
  const q = hash.indexOf("?");
  return new URLSearchParams(q >= 0 ? hash.slice(q + 1) : "");
}

/** 주소 해시 → 칩. 모르는 view 값은 기본(종합) */
export function detectViewFromHash(hash: string): DetectView {
  const params = paramsOf(hash);
  const view = params.get("view");
  const known = DETECT_VIEWS.find((v) => v.key === view);
  if (known) return known.key;
  if (view === null && EXPLORER_PARAMS.some((k) => params.has(k))) return "youtube";
  return "all";
}

/** 칩을 바꾼 해시. 탭 이름(#detect)과 다른 파라미터는 그대로 둔다 */
export function hashWithView(hash: string, view: DetectView): string {
  const q = hash.indexOf("?");
  const head = (q >= 0 ? hash.slice(0, q) : hash) || "#detect";
  const params = paramsOf(hash);
  params.set("view", view);
  return `${head}?${params.toString()}`;
}

/** 지금 해시의 view 값 (없으면 null). 영상 탐색이 조건 해시를 다시 쓸 때 칩을 잃지 않게 */
export function viewParam(hash: string): string | null {
  return paramsOf(hash).get("view");
}
