import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Tabs } from "@/components/connect/parts";
import { shortDateTime } from "@/lib/format";
import { RECORD_ID, getCreativeRecord, listCreativeRecords, redrawBoardImage, thumbSrc, type CreativeRecord, type CreativeRecordSummary } from "@/lib/creativeRecords";
import { thumbChip } from "@/lib/creativeThumbnails";
import { listRequestVideos } from "@/lib/requestVideos";
import { REQUEST_VIDEOS_PATH, statusKo, type RequestVideo } from "@/lib/requestVideoView";
import { SheetBoard } from "./SheetBoard";
import { StoryboardView } from "./StoryboardMaker";
import { PromptBox } from "./PromptParts";
import { RenderPanel } from "./RenderPanel";
import { RequestVideos } from "./RequestVideos";
import { ScriptPanel } from "./ScriptPanel";
import { RecordBoardFromScript, RecordScriptWrite } from "./RecordScriptStart";
import { boardBehindScript, nearestCount, stageChips } from "@/lib/creativeScript";

// 마케팅 › 제작 기록 (/marketing/records). 탭 세 개:
// - 전체(시간순, 기본): 이미지 시트·스토리보드 기록과 의뢰로 만든 영상을 만든 시각 하나로 모아 최신순으로 본다
//   (11/25, 두 길이 둘로 나뉘어 보이던 것을 합침). 영상 줄을 누르면 의뢰로 만든 영상 탭으로 가 그 영상에 스크롤한다
// - 이미지 시트 · 스토리보드: SNS·미디어 탭에서 만든 이미지 시트와 이어 만든 대본·스토리보드를 기록마다 다시 본다.
//   상세의 대본은 고쳐서 저장하거나 AI로 다시 쓸 수 있다(11/23). 목록에는 단계 칩(대본·스토리보드)
//   목록을 누르면 ?id=CR-… 로 상세가 열린다(상세의 RenderPanel에서 영상·유튜브 올리기)
// - 의뢰로 만든 영상(?view=videos): 총괄 홍보 영상 의뢰(wr_…)가 만든 AI 표시 영상. 영상마다 유튜브 올리기(사람 승인).
//   ?request=wr_… 로 들어오면 이 탭을 열고 그 의뢰 영상을 맨 앞에 둔다(11/23, SNS·미디어에서 옮겨 옴)

function Detail({ id }: { id: string }) {
  const [rec, setRec] = useState<CreativeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setRec(null);
    setError(null);
    getCreativeRecord(id).then((r) => live && setRec(r), (e: unknown) => live && setError(e instanceof Error ? e.message : "제작 기록을 읽지 못했어요"));
    return () => { live = false; };
  }, [id]);
  if (error) return <p className="text-sm text-accent" role="alert">{error}</p>;
  if (!rec) return <p className="text-sm text-ink-500">불러오는 중…</p>;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link to="/marketing/records" className="btn-ghost text-xs">‹ 기록 목록</Link>
        <h2 className="text-lg font-semibold tabular-nums">{rec.record_id}</h2>
        <span className="text-xs text-ink-500">{shortDateTime(rec.created_at)} 이미지 시트{rec.script?.at ? ` · ${shortDateTime(rec.script.at)} 대본` : ""}{rec.storyboard_at ? ` · ${shortDateTime(rec.storyboard_at)} 스토리보드` : ""}</span>
      </div>
      {(rec.note || rec.story) && (
        <dl className="grid gap-x-4 gap-y-1 text-sm md:grid-cols-[6rem_1fr]">
          {rec.note && <><dt className="text-ink-500">캐릭터 메모</dt><dd className="text-ink-900">{rec.note}</dd></>}
          {rec.story && <><dt className="text-ink-500">줄거리</dt><dd className="text-ink-900">{rec.story}</dd></>}
        </dl>
      )}
      <RenderPanel recordId={rec.record_id} />
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-ink-900">이미지 시트</h3>
        <SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} />
        <details>
          <summary className="cursor-pointer text-sm text-ink-500">이미지 시트 프롬프트</summary>
          <div className="space-y-3 pt-3">
            <PromptBox title="영어 이미지 프롬프트" text={rec.sheet.prompt_en} />
            <PromptBox title="네거티브 프롬프트" text={rec.sheet.negative_prompt_en} />
          </div>
        </details>
      </section>
      <details className="space-y-3" open>
        <summary className="cursor-pointer text-sm font-semibold text-ink-900">대본</summary>
        <div className="pt-3">
          {rec.script
            ? <ScriptPanel recordId={rec.record_id} script={rec.script} scenes={nearestCount(rec.script.scenes.length)} onChange={(script) => setRec({ ...rec, script })} />
            : <RecordScriptWrite recordId={rec.record_id} story={rec.story ?? ""} scenes={rec.storyboard?.shots.length ?? 6}
                onWritten={(script, story) => setRec({ ...rec, script, story })} />}
          {rec.script && boardBehindScript(rec) && (
            <div className="mt-3">
              <RecordBoardFromScript recordId={rec.record_id} story={rec.story ?? rec.script.title_ko} characterEn={rec.sheet.consistency_en}
                scenes={rec.script.scenes.length} onRecord={setRec} />
            </div>
          )}
        </div>
      </details>
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-ink-900">스토리보드</h3>
        {rec.storyboard ? <StoryboardView r={rec.storyboard} onRedraw={async () => { await redrawBoardImage(rec.record_id); }} /> : <p className="text-sm text-ink-500">이 기록에는 스토리보드가 없어요. SNS·미디어에서 줄거리를 넣고 다시 만들면 붙어요.</p>}
      </section>
    </div>
  );
}

function Row({ r }: { r: CreativeRecordSummary }) {
  const src = thumbSrc(r.thumbnail_base64);
  const thumb = thumbChip(r.render?.thumbnails);
  return (
    <li>
      <Link to={`/marketing/records?id=${r.record_id}`} className="flex items-center gap-4 rounded-2xl px-3 py-3 hover:bg-ink-50">
        <span className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-surface ring-1 ring-ink-100">
          {src ? <img src={src} alt="" className="h-full w-full object-contain" /> : <span className="text-xs text-ink-500">없음</span>}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink-900">{r.storyboard_title_ko ?? r.summary_ko}</span>
          <span className="block truncate text-xs text-ink-500">{r.story ?? r.note ?? r.summary_ko}</span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1 text-xs">
          <span className="flex gap-1">
            {stageChips(r).map((c) => <span key={c} className={`chip ${c === "시트만" ? "bg-ink-50 text-ink-500" : "bg-brand-50 text-brand-700"}`}>{c}</span>)}
          </span>
          {thumb && <span className={`chip ${r.render?.thumbnails === "done" ? "bg-emerald-50 text-emerald-800" : "bg-ink-50 text-ink-700"}`}>{thumb}</span>}
          <span className="tabular-nums text-ink-500">{shortDateTime(r.created_at)}</span>
        </span>
      </Link>
    </li>
  );
}

/** 전체(시간순) 목록의 영상 줄. 의뢰로 만든 영상 탭으로 가 그 영상에 스크롤·테두리 표시한다(RequestVideos의 기존 동작, 11/23) */
function VideoRow({ v }: { v: RequestVideo }) {
  return (
    <li>
      <Link to={`${REQUEST_VIDEOS_PATH}&request=${encodeURIComponent(v.request_id)}`} className="flex items-center gap-4 rounded-2xl px-3 py-3 hover:bg-ink-50">
        <span className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-surface text-xs text-ink-500 ring-1 ring-ink-100">영상</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink-900">{v.caption_ko ?? v.request_id}</span>
          <span className="block truncate text-xs text-ink-500">의뢰 {v.request_id}</span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1 text-xs">
          <span className="chip bg-ink-50 text-ink-700">{statusKo(v.status)}</span>
          <span className="tabular-nums text-ink-500">{shortDateTime(v.created_at)}</span>
        </span>
      </Link>
    </li>
  );
}

type MergedRow = { kind: "record"; at: number; r: CreativeRecordSummary } | { kind: "video"; at: number; v: RequestVideo };

/** 이미지 시트·스토리보드 기록과 의뢰로 만든 영상을 만든 시각(created_at) 기준 최신순으로 합친다 */
function mergeByTime(items: CreativeRecordSummary[], videos: RequestVideo[]): MergedRow[] {
  const a: MergedRow[] = items.map((r) => ({ kind: "record", at: new Date(r.created_at).getTime(), r }));
  const b: MergedRow[] = videos.map((v) => ({ kind: "video", at: new Date(v.created_at).getTime(), v }));
  return [...a, ...b].sort((x, y) => y.at - x.at);
}

type View = "all" | "records" | "videos";
const VIEW_TABS: readonly { id: View; label: string }[] = [
  { id: "all", label: "전체(시간순)" },
  { id: "records", label: "이미지 시트 · 스토리보드" },
  { id: "videos", label: "의뢰로 만든 영상" },
];

export function CreativeRecords() {
  const [params, setParams] = useSearchParams();
  const id = params.get("id");
  const askedView = params.get("view");
  const view: View = askedView === "videos" || (params.get("request") && !id) ? "videos" : askedView === "records" ? "records" : "all";
  const pick = (next: View) => setParams(next === "all" ? {} : { view: next }, { replace: true });
  const [items, setItems] = useState<CreativeRecordSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 전체(시간순) 탭에서 합칠 의뢰 영상 목록. 이미지 시트·스토리보드와 오류를 따로 가진다(하나가 막혀도 나머지는 본다)
  const [videos, setVideos] = useState<RequestVideo[] | null>(null);
  const [videosError, setVideosError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setItems(await listCreativeRecords(100));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "제작 기록을 읽지 못했어요");
    }
    try {
      setVideos(await listRequestVideos(null, 100));
      setVideosError(null);
    } catch (e) {
      setVideosError(e instanceof Error ? e.message : "의뢰로 만든 영상을 읽지 못했어요");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">제작 기록</h1>
          <p className="mt-1 text-sm text-ink-500">SNS·미디어에서 만든 이미지 시트·스토리보드와 총괄 홍보 영상 의뢰로 만든 영상을 다시 보고, 회사 유튜브에 올려요(올리기는 늘 사람 승인). 기록은 최근 200건까지 남고, 올린 원본 이미지는 저장하지 않아 작은 미리보기만 보여요.</p>
        </div>
        <Link to="/marketing/sns" className="btn-ghost ml-auto text-xs">새로 만들기 ›</Link>
      </header>
      <Tabs label="제작 기록 보기" items={VIEW_TABS} value={view} onPick={pick} />
      <section className="card p-6" role="tabpanel" aria-label={view === "videos" ? "의뢰로 만든 영상" : view === "records" ? "이미지 시트 · 스토리보드" : "전체(시간순)"}>
        {view === "videos" ? <RequestVideos />
          : id && RECORD_ID.test(id) ? <Detail id={id} />
          : view === "all" ? (
            error ? <p className="text-sm text-accent" role="alert">{error}</p>
            : videosError ? <p className="text-sm text-accent" role="alert">{videosError}</p>
            : items === null || videos === null ? <p className="text-sm text-ink-500">불러오는 중…</p>
            : items.length === 0 && videos.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">아직 기록이 없어요. SNS·미디어에서 이미지 시트를 만들거나, 총괄에 홍보 영상을 의뢰하면 여기에 쌓여요.</p>
            : (
              <ul className="divide-y divide-ink-100" aria-label="제작 기록 전체(시간순)">
                {mergeByTime(items, videos).map((row) => row.kind === "record"
                  ? <Row key={`r-${row.r.record_id}`} r={row.r} />
                  : <VideoRow key={`v-${row.v.post_draft_id}`} v={row.v} />)}
              </ul>
            )
          )
          : error ? <p className="text-sm text-accent" role="alert">{error}</p>
          : items === null ? <p className="text-sm text-ink-500">불러오는 중…</p>
          : items.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">아직 기록이 없어요. SNS·미디어에서 이미지 시트를 만들면 여기에 쌓여요.</p>
          : <ul className="divide-y divide-ink-100" aria-label="제작 기록 목록">{items.map((r) => <Row key={r.record_id} r={r} />)}</ul>}
      </section>
    </div>
  );
}
