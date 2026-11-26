// 마케팅 › 제작 기록: 작업 한 건을 한 줄로 (2026-11-26). 순수 계산만 둔다. node --test (recordWorks.test.ts)
// 이미지 시트·스토리보드 기록과 총괄 의뢰 영상을 따로 나누지 않는다. 캐릭터 기록으로 만든 의뢰 영상은
// 그 기록 줄에 합치고, 기록 없이 만든 의뢰 영상은 혼자 한 줄이다.
import type { CreativeRecordSummary } from "./creativeRecords";
import { statusKo, type RequestVideo } from "./requestVideoView.ts";

export const RECORDS_PATH = "/marketing/records";

const LINKED = /^med_(CR-[0-9a-f]{12})$/;

/** 캐릭터 기록으로 만든 의뢰 영상은 media_id가 `med_<기록 번호>`다(마케팅 `request_creative._write_meta`) */
export const linkedRecordId = (v: Pick<RequestVideo, "media_id">): string | null => LINKED.exec(v.media_id ?? "")?.[1] ?? null;

export interface Work {
  key: string;
  /** 제작 기록 번호. 기록 없이 만든 의뢰 영상이면 null */
  recordId: string | null;
  /** 목록에서 읽은 기록 요약. 목록 밖(오래된 기록)이면 null */
  record: CreativeRecordSummary | null;
  /** 이 작업의 의뢰 영상(최신 먼저) */
  videos: RequestVideo[];
  /** 가장 최근에 만든 시각(정렬·날짜 묶음) */
  at: string;
}

const time = (s: string) => {
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : 0;
};

export function mergeWorks(records: CreativeRecordSummary[], videos: RequestVideo[]): Work[] {
  const byKey = new Map<string, Work>(
    records.map((r) => [r.record_id, { key: r.record_id, recordId: r.record_id, record: r, videos: [], at: r.created_at }]),
  );
  for (const v of [...videos].sort((a, b) => time(b.created_at) - time(a.created_at))) {
    const recordId = linkedRecordId(v);
    const key = recordId ?? v.post_draft_id;
    const found = byKey.get(key);
    if (!found) {
      byKey.set(key, { key, recordId, record: null, videos: [v], at: v.created_at });
      continue;
    }
    found.videos.push(v);
    if (time(v.created_at) > time(found.at)) found.at = v.created_at;
  }
  return [...byKey.values()].sort((a, b) => time(b.at) - time(a.at));
}

/** "11월 25일" (보는 사람 시각) */
export function dayLabel(at: string): string {
  const d = new Date(at);
  return Number.isFinite(d.getTime()) ? `${d.getMonth() + 1}월 ${d.getDate()}일` : "날짜 모름";
}

// ---- 목록 페이지: 10건씩, 주소는 ?page=2 (1쪽은 붙이지 않는다)

export const PAGE_SIZE = 10;

export const pageCount = (total: number): number => Math.max(1, Math.ceil(total / PAGE_SIZE));

/** 주소의 ?page= 값 → 있는 쪽 번호. 모르는 값이면 1, 끝을 넘으면 마지막 쪽 */
export function pageOf(param: string | null | undefined, total: number): number {
  const n = Number(param);
  return Number.isInteger(n) && n > 1 ? Math.min(n, pageCount(total)) : 1;
}

export const pageItems = <T,>(items: T[], page: number): T[] => items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

export const pageHref = (page: number): string => (page > 1 ? `${RECORDS_PATH}?page=${page}` : RECORDS_PATH);

/** 최신순으로 정렬된 작업을 날짜별로 묶는다 */
export function groupByDay(works: Work[]): { day: string; works: Work[] }[] {
  const groups: { day: string; works: Work[] }[] = [];
  for (const w of works) {
    const day = dayLabel(w.at);
    const last = groups[groups.length - 1];
    if (last?.day === day) last.works.push(w);
    else groups.push({ day, works: [w] });
  }
  return groups;
}

export const workHref = (w: Pick<Work, "recordId" | "videos">): string =>
  w.recordId ? `${RECORDS_PATH}?id=${encodeURIComponent(w.recordId)}` : `${RECORDS_PATH}?request=${encodeURIComponent(w.videos[0]?.request_id ?? "")}`;

const firstLine = (s: string | null | undefined) => (s ?? "").split("\n").map((l) => l.trim()).find(Boolean) ?? "";

export function workTitle(w: Pick<Work, "record" | "videos" | "key">): string {
  return w.record?.storyboard_title_ko || w.record?.summary_ko || firstLine(w.videos[0]?.caption_ko) || w.videos[0]?.request_id || w.key;
}

export type Tone = "ok" | "wait" | "bad" | "muted";
export interface WorkStatus { text: string; tone: Tone }

/** 상태를 정하는 데 필요한 기록 값. `render`는 칸마다 상태 글자 */
export interface RecordFacts { render?: Record<string, string>; shot_count?: number; script_scenes?: number }

/** 목록 칸에 그릴 만든 그림(11/26): render의 sheet_image·board_image가 "done"일 때만 읽는다.
 *  AI 마네킹 SVG 콘티만 있으면 목록 응답에 그림이 없어 null(글자 칸 그대로) */
export type MadePicturePart = "sheet_image" | "board_image";
export const madePicture = (r: Pick<RecordFacts, "render"> | null, part: MadePicturePart): MadePicturePart | null =>
  r?.render?.[part] === "done" ? part : null;

/** 칸 종류: 만든 그림을 받았으면 그림, 없거나 받는 중이거나 실패면 지금 모양(원본 썸네일·글자) */
export function slotKind(r: Pick<RecordFacts, "render"> | null, part: MadePicturePart, media: { src: string | null; failed: boolean }): "picture" | "original" {
  return madePicture(r, part) && media.src && !media.failed ? "picture" : "original";
}

/** 기록의 render 객체 → 칸마다 상태 글자(목록 요약과 같은 모양) */
export function renderStatuses(render: object | null | undefined): Record<string, string> {
  return Object.fromEntries(
    Object.entries(render ?? {}).map(([k, v]) => [k, String((v as { status?: unknown } | null)?.status ?? "")]),
  );
}

const PUBLISH_WAIT = new Set(["awaiting_approval"]);
const PUBLISHING = new Set(["uploading", "posting"]);
const VIDEO_MAKING = new Set(["running", "uploading"]);
const VIDEO_STOPPED = new Set(["failed", "rejected", "blocked", "unavailable"]);

const publishStates = (facts: RecordFacts | null, videos: Pick<RequestVideo, "youtube" | "instagram">[]) => [
  facts?.render?.youtube ?? "", facts?.render?.instagram ?? "",
  ...videos.flatMap((v) => [v.youtube?.status ?? "", v.instagram?.status ?? ""]),
];

export function workStatus(facts: RecordFacts | null, videos: Pick<RequestVideo, "status" | "youtube" | "instagram">[]): WorkStatus {
  const publish = publishStates(facts, videos);
  if (publish.some((s) => PUBLISH_WAIT.has(s))) return { text: "올리기 승인 대기", tone: "wait" };
  if (publish.some((s) => PUBLISHING.has(s))) return { text: "올리는 중", tone: "wait" };
  if (publish.includes("done")) return { text: "게시함", tone: "ok" };
  const video = facts?.render?.video ?? "";
  if (video === "awaiting_approval") return { text: "영상 승인 대기", tone: "wait" };
  if (VIDEO_MAKING.has(video)) return { text: "영상 만드는 중", tone: "wait" };
  if (video === "done" || facts?.render?.final_video === "done" || videos.some((v) => v.status === "draft_ready")) return { text: "영상 완성", tone: "ok" };
  if (videos.length > 0) return { text: statusKo(videos[0].status), tone: videos[0].status === "check_rejected" ? "bad" : "wait" };
  if (VIDEO_STOPPED.has(video)) return { text: "영상 다시 필요", tone: "bad" };
  if (facts?.shot_count) return { text: "다음: 영상 만들기", tone: "muted" };
  if (facts?.script_scenes) return { text: "다음: 스토리보드", tone: "muted" };
  return { text: "다음: 대본 쓰기", tone: "muted" };
}

export type VideoCover =
  | { from: "request"; draftId: string; mediaId: string }
  | { from: "record"; recordId: string; mediaId: string };

export interface VideoSlot { state: "done" | "making" | "none"; cover: VideoCover | null; seconds: number | null }

/** 목록의 영상 칸. 그림은 의뢰 영상의 정해진 썸네일이 먼저, 없으면 기록 썸네일 */
export function videoSlot(w: Work): VideoSlot {
  const render = w.record?.render ?? {};
  const ready = w.videos.find((v) => v.labeled);
  const requestCover = ready?.cover_media_id ?? ready?.covers?.[0]?.media_id ?? null;
  const recordCover = w.record?.cover_media_id ?? null;
  const cover: VideoCover | null = ready && requestCover
    ? { from: "request", draftId: ready.post_draft_id, mediaId: requestCover }
    : w.recordId && recordCover ? { from: "record", recordId: w.recordId, mediaId: recordCover } : null;
  const seconds = w.videos[0]?.duration_s ?? w.record?.video_seconds ?? null;
  if (w.videos.length > 0 || render.video === "done" || render.final_video === "done") return { state: "done", cover, seconds };
  if (render.video === "awaiting_approval" || VIDEO_MAKING.has(render.video ?? "")) return { state: "making", cover: null, seconds: null };
  return { state: "none", cover: null, seconds: null };
}

// ---- 상세: 만드는 순서 네 단계

export type StepNo = 1 | 2 | 3 | 4;

/** 처음 펼칠 단계 하나: 올리기가 진행 중이면 4, 영상이 있거나 스토리보드가 있으면 3, 아니면 2 */
export function defaultStep(facts: RecordFacts | null, videos: Pick<RequestVideo, "youtube" | "instagram">[]): StepNo {
  if (publishStates(facts, videos).some((s) => PUBLISH_WAIT.has(s) || PUBLISHING.has(s))) return 4;
  if (videos.length > 0 || facts?.render?.video || facts?.render?.final_video || facts?.shot_count) return 3;
  return 2;
}

interface PublishState { status?: string | null; title?: string | null; caption?: string | null }

/** 기록 쪽에 이미 남은 유튜브·인스타 상태(초안 포함)가 있나 */
export const hasPublishState = (render: { youtube?: PublishState | null; instagram?: PublishState | null } | null | undefined): boolean =>
  !!(render?.youtube?.status || render?.youtube?.title || render?.instagram?.status || render?.instagram?.caption);
