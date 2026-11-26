import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router";
import { shortDateTime } from "@/lib/format";
import { redrawBoardImage, thumbSrc, type CreativeRecord } from "@/lib/creativeRecords";
import { boardBehindScript, boardMissing, nearestCount } from "@/lib/creativeScript";
import { isSvgBoard } from "@/lib/storyboardSvg";
import { usesOriginalReference } from "@/lib/videoAsk";
import { listRequestVideos } from "@/lib/requestVideos";
import { VIDEO_NOT_FOUND_KO, canUploadToYoutube, durationKo, type RequestVideo } from "@/lib/requestVideoView";
import { RECORDS_PATH, defaultSteps, hasPublishState, linkedRecordId, renderStatuses, workStatus, workTitle, type StepNo } from "@/lib/recordWorks";
import { DetailHeader, Fold, Step } from "./RecordSteps";
import { BoardPicture, FinalVideoArea, Keyframes, MotionArea, RecordPublish, RecordThumbnails, SheetPicture, VideoArea, useRecordRender, type RecordRender } from "./RenderPanel";
import { RequestVideoCard, RequestVideoPublish } from "./RequestVideoParts";
import { RecordBoardFromScript, RecordScriptWrite } from "./RecordScriptStart";
import { ScriptPanel } from "./ScriptPanel";
import { SheetBoard } from "./SheetBoard";
import { StoryboardView } from "./StoryboardMaker";
import { PromptBox } from "./PromptParts";
import { PromoSitePanel } from "./PromoSitePanel";
import { isHolding, promoChip, promoItem, usePromoSite } from "@/lib/promoSite";

// 제작 기록 상세 (2026-11-26). 만드는 순서 네 단계를 접이식으로 보여 주고, 지금 볼 단계 하나만 펼친다.
// 1 이미지 시트 → 2 대본·스토리보드 → 3 영상 → 4 올리기. 이 작업의 총괄 의뢰 영상도 3·4단계에 함께 둔다.

const WEB_NOTE = "Higgsfield 화면(웹 구독)에서 2단계의 스토리보드 그림을 시작 이미지로 올리고 프롬프트를 붙여 넣어 만드세요. 만든 영상은 위 영상 칸에 끌어 놓거나 [파일 올리기]로 붙이면 완성 영상이 돼요.";
const PENDING_VIDEO = new Set(["awaiting_approval", "running", "uploading"]);

/** 처음에는 지금 볼 단계(영상 전이면 1·2)만 펼친다. 그 뒤는 사람이 여닫은 대로 */
function useSteps(first: StepNo[] | null) {
  const [open, setOpen] = useState<ReadonlySet<StepNo> | null>(null);
  const key = first?.join(",") ?? null;
  useEffect(() => {
    if (key !== null) setOpen((prev) => prev ?? new Set(key.split(",").map(Number) as StepNo[]));
  }, [key]);
  const toggle = (no: StepNo) => setOpen((prev) => {
    const next = new Set(prev);
    if (!next.delete(no)) next.add(no);
    return next;
  });
  return { isOpen: (no: StepNo) => open?.has(no) ?? false, toggle };
}

/** 의뢰 영상 목록. `pick`으로 이 화면의 것만 남긴다 */
function useVideos(requestId: string | null, pick: (v: RequestVideo) => boolean) {
  const [videos, setVideos] = useState<RequestVideo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setVideos(await listRequestVideos(requestId, 100));
      setError(null);
    } catch (e) {
      setVideos((prev) => prev ?? []);
      setError(e instanceof Error ? e.message : "의뢰로 만든 영상을 읽지 못했어요");
    }
  }, [requestId]);
  useEffect(() => { void load(); }, [load]);
  return { videos: videos?.filter(pick) ?? null, error, load };
}

function SheetStep({ ctx, rec }: { ctx: RecordRender; rec: CreativeRecord }) {
  const drawn = ctx.render.sheet_image?.status === "done";
  return (
    <>
      {rec.note && <p className="text-sm text-ink-900"><span className="text-xs text-ink-500">캐릭터 메모 </span>{rec.note}</p>}
      <div className="max-w-sm"><SheetPicture ctx={ctx} /></div>
      <Fold title="배치판과 색" open={!drawn}><SheetBoard r={rec.sheet} thumb={thumbSrc(rec.thumbnail_base64)} /></Fold>
      <Fold title="이미지 시트 프롬프트">
        <PromptBox title="영어 이미지 프롬프트" text={rec.sheet.prompt_en} />
        <PromptBox title="네거티브 프롬프트" text={rec.sheet.negative_prompt_en} />
      </Fold>
    </>
  );
}

function BoardStep({ ctx, rec }: { ctx: RecordRender; rec: CreativeRecord }) {
  const { render, keyframed, setRec, load } = ctx;
  // 가상 인물·그림 캐릭터와 키프레임 방식은 스토리보드 그림이 필요 없다. 만든 적이 없으면 빈 칸 대신 접어 둔다
  const needsPicture = !!render.board_image?.status || (!keyframed && !usesOriginalReference(rec.sheet.kind));
  // 실제 그림이나 영상이 생기면 회색 구도 그림 콘티는 접는다(AI가 그린 SVG 콘티는 펼쳐 둔다)
  const drawn = keyframed || [render.board_image, render.video, render.final_video].some((p) => p?.status === "done");
  const board = rec.storyboard;
  // 대본만 있고 스토리보드가 없으면(11/26) 처음 만들기 버튼만 두고 옛 안내·빈 그림 칸은 숨긴다. 이미 그림을 올렸으면 그림 칸은 그대로
  const missing = boardMissing(rec);
  const hidePicture = missing && !render.board_image?.status;
  const view = board && <StoryboardView r={board} onRedraw={async () => { await redrawBoardImage(rec.record_id); await load(); }} />;
  return (
    <>
      {rec.script && rec.story && <p className="text-sm text-ink-900"><span className="text-xs text-ink-500">줄거리 </span>{rec.story}</p>}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-ink-900">대본</h3>
        {rec.script
          ? <ScriptPanel recordId={rec.record_id} script={rec.script} scenes={nearestCount(rec.script.scenes.length)}
              onChange={(script) => setRec((prev) => prev && { ...prev, script })} />
          : <RecordScriptWrite recordId={rec.record_id} story={rec.story ?? ""} scenes={board?.shots.length ?? 6}
              onWritten={(script, story) => setRec((prev) => prev && { ...prev, script, story })} />}
        {rec.script && boardBehindScript(rec) && (
          <RecordBoardFromScript recordId={rec.record_id} story={rec.story ?? rec.script.title_ko} characterEn={rec.sheet.consistency_en}
            scenes={rec.script.scenes.length} onRecord={setRec} />
        )}
      </div>
      <div className="space-y-3 border-t border-ink-100 pt-4">
        <h3 className="text-sm font-semibold text-ink-900">스토리보드</h3>
        {rec.script && missing && (
          <RecordBoardFromScript first recordId={rec.record_id} story={rec.story ?? rec.script.title_ko} characterEn={rec.sheet.consistency_en}
            scenes={rec.script.scenes.length} onRecord={setRec} />
        )}
        {needsPicture && !hidePicture && <div className="max-w-2xl"><BoardPicture ctx={ctx} /></div>}
        <Keyframes ctx={ctx} />
        {missing ? null : !board ? <p className="text-sm text-ink-500">이 기록에는 스토리보드가 없어요. SNS·미디어에서 줄거리를 넣고 다시 만들면 붙어요.</p>
          : drawn && !isSvgBoard(board) ? <Fold title={`칸 설명과 프롬프트 (${board.shots.length}칸)`}>{view}</Fold>
            : view}
        {!needsPicture && !hidePicture && <Fold title="스토리보드 그림 직접 올리기 (선택)"><div className="max-w-2xl"><BoardPicture ctx={ctx} /></div></Fold>}
      </div>
    </>
  );
}

function VideoStep({ ctx, videos, showRecordThumbnails, onVideos }: { ctx: RecordRender; videos: RequestVideo[]; showRecordThumbnails: boolean; onVideos: () => void }) {
  const { render } = ctx;
  const finalDone = render.final_video?.status === "done";
  const videoDone = render.video?.status === "done";
  const making = PENDING_VIDEO.has(render.video?.status ?? "");
  return (
    <>
      {((!finalDone && !videoDone) || render.motion_video) && (
        <Fold title="모션 참조 영상 (선택)" open={!!render.motion_video && !videoDone}><MotionArea ctx={ctx} /></Fold>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-3">
          {finalDone ? (
            <>
              <FinalVideoArea ctx={ctx} />
              <Fold title="처음 만든 영상 (스토리보드 영상)" open={making}><VideoArea ctx={ctx} webNote={WEB_NOTE} /></Fold>
            </>
          ) : <VideoArea ctx={ctx} label="영상" uploadPart="final_video" webNote={WEB_NOTE} />}
        </div>
        <div className="space-y-4">
          {showRecordThumbnails && <RecordThumbnails ctx={ctx} />}
          {videos.map((v) => <RequestVideoCard key={v.post_draft_id} v={v} onChanged={onVideos} />)}
        </div>
      </div>
    </>
  );
}

export function RecordDetail({ recordId }: { recordId: string }) {
  const ctx = useRecordRender(recordId);
  const { rec, error, render } = ctx;
  const mine = useVideos(null, (v) => linkedRecordId(v) === recordId);
  const promo = usePromoSite();
  const site = promo.status;
  const videos = mine.videos;
  const facts = rec ? { render: renderStatuses(rec.render), shot_count: rec.storyboard?.shots.length ?? 0, script_scenes: rec.script?.scenes.length ?? 0 } : null;
  const steps = useSteps(facts && videos ? defaultSteps(facts, videos) : null);

  if (!rec) {
    if (!error) return <p className="text-sm text-ink-500">불러오는 중…</p>;
    // 오래돼 지워진 기록이라도 의뢰 영상은 남아 있으면 보여 준다
    if (videos && videos.length > 0) return <VideoOnly videos={videos} onChanged={() => void mine.load()} />;
    return <p className="text-sm text-accent" role="alert">{error}</p>;
  }
  if (!videos || !facts) return <p className="text-sm text-ink-500">불러오는 중…</p>;

  const labeled = videos.filter(canUploadToYoutube);
  // 의뢰 영상(AI 표시 포함)이 있으면 그 영상을 올린다. 기록 쪽에 이미 남은 올리기 상태가 있으면 그것도 보여 준다
  const recordPublish = hasPublishState(render) || labeled.length === 0;
  const recordVideo = render.video?.status === "done" || render.final_video?.status === "done";
  const anyVideo = recordVideo || labeled.length > 0;
  const status = workStatus(facts, videos);
  // 완성 영상을 직접 올렸으면 길이를 모른다. 배속한 영상은 재생 길이로 보인다
  const made = render.video?.summary;
  const speed = render.overlay?.speed_applied && made?.speed ? made.speed : 1;
  const seconds = labeled[0]?.duration_s ?? (render.final_video?.status !== "done" && made?.seconds ? made.seconds / speed : null);
  const scenes = rec.script?.scenes.length;
  const shots = rec.storyboard?.shots.length;
  const title = workTitle({ record: { record_id: recordId, created_at: rec.created_at, summary_ko: rec.sheet.summary_ko, storyboard_title_ko: rec.storyboard?.title_ko, shot_count: shots ?? 0 }, videos, key: recordId });
  const promoted = promoChip(promo.status, [recordId, ...videos.map((v) => v.post_draft_id)]);
  // 홍보 사이트 칸: 올리는 영상과 같은 규칙. 이미 올린 기록 영상은 의뢰 영상이 생겨도 보여 준다(내릴 수 있게)
  const recordPromo = (recordVideo && recordPublish) || isHolding(promoItem(promo.status, recordId));
  return (
    <div className="space-y-4">
      <DetailHeader title={title} status={status} meta={`${shortDateTime(rec.created_at)} · ${recordId}`} />
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
      {mine.error && <p className="text-xs text-accent" role="alert">{mine.error}</p>}
      <Step no={1} title="이미지 시트" done open={steps.isOpen(1)} onToggle={() => steps.toggle(1)}
        summary={render.sheet_image?.status === "done" ? "캐릭터 그림 있음" : "캐릭터 정리 완료"}>
        <SheetStep ctx={ctx} rec={rec} />
      </Step>
      <Step no={2} title="대본·스토리보드" done={!!rec.storyboard} open={steps.isOpen(2)} onToggle={() => steps.toggle(2)}
        summary={[scenes ? `대본 ${scenes}장면` : "대본 없음", shots ? `스토리보드 ${shots}칸` : "스토리보드 없음"].join(", ")}>
        <BoardStep ctx={ctx} rec={rec} />
      </Step>
      <Step no={3} title="영상" done={anyVideo} open={steps.isOpen(3)} onToggle={() => steps.toggle(3)}
        summary={anyVideo ? (seconds ? durationKo(seconds) : "영상 있음") : status.text}>
        <VideoStep ctx={ctx} videos={videos} showRecordThumbnails={recordPublish || !!render.thumbnails?.status} onVideos={() => void mine.load()} />
      </Step>
      <Step no={4} title="올리기" done={status.text === "게시함"} locked={!anyVideo && !hasPublishState(render)} open={steps.isOpen(4)} onToggle={() => steps.toggle(4)}
        summary={!anyVideo && !hasPublishState(render) ? "영상이 생기면 열려요" : promoted ? `${promoted.text} · 유튜브, 인스타그램` : "유튜브, 인스타그램, 홍보 사이트 · 사람 승인 필요"}>
        {promo.error && <p className="text-xs text-ink-500">{promo.error}</p>}
        {site && labeled.map((v) => (
          <PromoSitePanel key={`promo-${v.post_draft_id}`} itemId={v.post_draft_id} title={title} label={`의뢰 영상 ${v.request_id}`} status={site} onChanged={() => void promo.load()} />
        ))}
        {site && recordPromo && (
          <PromoSitePanel itemId={recordId} title={title} label={labeled.length > 0 ? "제작 기록 영상" : undefined} status={site} onChanged={() => void promo.load()} />
        )}
        {labeled.map((v) => <RequestVideoPublish key={v.post_draft_id} v={v} onChanged={() => void mine.load()} />)}
        {recordPublish && (
          <div className="space-y-3">
            {labeled.length > 0 && <p className="text-xs font-semibold text-ink-900">제작 기록 영상</p>}
            <RecordPublish ctx={ctx} />
          </div>
        )}
      </Step>
    </div>
  );
}

/** 기록 없이 의뢰로만 만든 영상: 1·2단계는 비어 있고 3·4단계만 쓴다 */
function VideoOnly({ videos, onChanged }: { videos: RequestVideo[]; onChanged: () => void }) {
  const steps = useSteps(defaultSteps(null, videos));
  const promo = usePromoSite();
  const site = promo.status;
  const labeled = videos.filter(canUploadToYoutube);
  const status = workStatus(null, videos);
  return (
    <div className="space-y-4">
      <DetailHeader title={workTitle({ record: null, videos, key: videos[0].request_id })} status={status} meta={`${shortDateTime(videos[0].created_at)} · 총괄 의뢰`} />
      <Step no={1} title="이미지 시트" done={false} locked open={false} onToggle={() => {}} summary="없음 · 총괄 의뢰로 바로 만든 영상" />
      <Step no={2} title="대본·스토리보드" done={false} locked open={false} onToggle={() => {}} summary="없음" />
      <Step no={3} title="영상" done={labeled.length > 0} open={steps.isOpen(3)} onToggle={() => steps.toggle(3)} summary={labeled[0] ? durationKo(labeled[0].duration_s) : status.text}>
        {videos.map((v) => <RequestVideoCard key={v.post_draft_id} v={v} onChanged={onChanged} />)}
      </Step>
      <Step no={4} title="올리기" done={status.text === "게시함"} locked={labeled.length === 0} open={steps.isOpen(4)} onToggle={() => steps.toggle(4)}
        summary={labeled.length === 0 ? "AI 표시가 입혀지면 열려요" : promoChip(promo.status, labeled.map((v) => v.post_draft_id))?.text ?? "유튜브, 인스타그램, 홍보 사이트 · 사람 승인 필요"}>
        {promo.error && <p className="text-xs text-ink-500">{promo.error}</p>}
        {site && labeled.map((v) => (
          <PromoSitePanel key={`promo-${v.post_draft_id}`} itemId={v.post_draft_id} title={workTitle({ record: null, videos: [v], key: v.request_id })}
            label={labeled.length > 1 ? `의뢰 영상 ${v.request_id}` : undefined} status={site} onChanged={() => void promo.load()} />
        ))}
        {labeled.map((v) => <RequestVideoPublish key={v.post_draft_id} v={v} onChanged={onChanged} />)}
      </Step>
    </div>
  );
}

/** `?request=wr_…`로 들어온 상세. 캐릭터 기록으로 만든 의뢰면 그 기록 상세로 보낸다 */
export function RequestDetail({ requestId }: { requestId: string }) {
  const found = useVideos(requestId, (v) => v.request_id === requestId);
  if (!found.videos) return <p className="text-sm text-ink-500">불러오는 중…</p>;
  // 이 의뢰의 영상이 모두 한 기록의 것일 때만 보낸다(섞여 있으면 여기서 다 보여 줘야 빠지는 영상이 없다)
  const linked = new Set(found.videos.map(linkedRecordId));
  const recordId = linked.size === 1 ? [...linked][0] : null;
  if (recordId) return <Navigate replace to={`${RECORDS_PATH}?id=${recordId}`} />;
  if (found.videos.length === 0) {
    return (
      <div className="space-y-4">
        <DetailHeader title={requestId} status={{ text: "영상 없음", tone: "muted" }} meta="총괄 의뢰" />
        {found.error ? <p className="text-sm text-accent" role="alert">{found.error}</p> : <p role="status" className="card p-5 text-sm text-ink-700">{VIDEO_NOT_FOUND_KO}</p>}
      </div>
    );
  }
  return <VideoOnly videos={found.videos} onChanged={() => void found.load()} />;
}
