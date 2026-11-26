import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useAuthedMedia } from "@/lib/authedMedia";
import { RECORD_ID, listCreativeRecords, thumbSrc, type CreativeRecordSummary } from "@/lib/creativeRecords";
import { thumbUrl } from "@/lib/creativeThumbnails";
import { listRequestVideos, requestVideoCoverUrl } from "@/lib/requestVideos";
import { durationKo, requestFilterOf, type RequestVideo } from "@/lib/requestVideoView";
import { groupByDay, mergeWorks, pageCount, pageHref, pageItems, pageOf, videoSlot, workHref, workStatus, workTitle, type VideoCover, type Work } from "@/lib/recordWorks";
import { RecordDetail, RequestDetail } from "./RecordDetail";
import { promoChip, usePromoSite, type PromoStatus } from "@/lib/promoSite";
import { StatusChip } from "./RecordSteps";

// 마케팅 › 제작 기록 (/marketing/records). 목록 하나(2026-11-26, 탭 세 개를 없앴다):
// - 작업 한 건이 한 줄이다. 열은 만드는 순서(이미지 시트 → 대본·스토리보드 → 영상)로 고정하고, 없는 단계는 점선 빈칸
// - 총괄 홍보 영상 의뢰가 만든 영상은 그 캐릭터 기록 줄의 영상 칸에 합친다. 기록 없이 만든 영상은 혼자 한 줄
// - 날짜별로 묶어 최신순, 10건씩 페이지 번호(?page=2). 줄을 누르면 단계별 상세(RecordDetail), 상세의 [‹ 제작 기록]은 보던 쪽으로
// - ?id=CR-… 기록 상세, ?request=wr_… 의뢰 영상 상세. 옛 주소의 ?view=는 목록으로 본다

const COLUMNS = "grid grid-cols-[minmax(0,1fr)_2.75rem_3.5rem_2.75rem] items-center gap-2 px-3 sm:grid-cols-[minmax(0,1fr)_6rem_9rem_6rem] sm:gap-4 sm:px-4";
const SLOT = "flex h-16 flex-col items-center justify-center gap-1 overflow-hidden rounded-xl text-center text-[10px] sm:h-32 sm:text-xs";
const FILLED = `${SLOT} bg-surface text-ink-700 ring-1 ring-ink-100`;

function Empty({ label }: { label: string }) {
  return <span className={`${SLOT} border border-dashed border-ink-300 text-ink-500`}>{label}</span>;
}

function SheetSlot({ record }: { record: CreativeRecordSummary | null }) {
  if (!record) return <Empty label="없음" />;
  const src = thumbSrc(record.thumbnail_base64);
  return <span className={FILLED}>{src ? <img src={src} alt="이미지 시트" className="h-full w-full object-cover" /> : "시트"}</span>;
}

function BoardSlot({ record }: { record: CreativeRecordSummary | null }) {
  if (!record) return <Empty label="없음" />;
  if (!record.shot_count && !record.script_scenes) return <Empty label="아직 없음" />;
  return (
    <span className={FILLED}>
      {record.script_scenes ? <span>대본 {record.script_scenes}장면</span> : null}
      {record.shot_count ? <span className="font-semibold text-ink-900">콘티 {record.shot_count}칸</span> : <span className="text-ink-500">콘티 전</span>}
    </span>
  );
}

const coverUrl = (c: VideoCover | null): string | null =>
  !c ? null : c.from === "request" ? requestVideoCoverUrl(c.draftId, c.mediaId) : thumbUrl(c.recordId, c.mediaId);

function VideoSlotCell({ work }: { work: Work }) {
  const slot = videoSlot(work);
  const { src } = useAuthedMedia(coverUrl(slot.cover));
  if (slot.state === "none") return <Empty label="아직 없음" />;
  if (slot.state === "making") return <span className={`${FILLED} animate-pulse`}>만드는 중</span>;
  const length = slot.seconds ? durationKo(slot.seconds) : "영상";
  return (
    <span className={`${FILLED} relative ring-2 ring-accent/50`}>
      {src && <img src={src} alt="" className="absolute inset-0 h-full w-full object-cover" />}
      <span className={`relative rounded px-1 ${src ? "mt-auto mb-1 bg-black/60 text-white" : "font-semibold text-ink-900"}`}>▶ {length}</span>
    </span>
  );
}

function Row({ work, page, promo }: { work: Work; page: number; promo: PromoStatus | null }) {
  const facts = work.record;
  const when = new Date(work.at);
  const promoted = promoChip(promo, [work.recordId, ...work.videos.map((v) => v.post_draft_id)]);
  return (
    <li>
      <Link to={workHref(work)} state={{ page }} className={`${COLUMNS} py-3 hover:bg-ink-50`}>
        <span className="min-w-0 space-y-1">
          <span className="block truncate text-sm font-medium text-ink-900">{workTitle(work)}</span>
          <span className="block truncate text-xs tabular-nums text-ink-500">
            {Number.isFinite(when.getTime()) ? when.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : ""}
            {work.videos.length > 0 ? " · 총괄 의뢰" : ""}
          </span>
          <span className="flex flex-wrap gap-1">
            <StatusChip {...workStatus(facts, work.videos)} />
            {promoted && <StatusChip {...promoted} />}
          </span>
        </span>
        <SheetSlot record={work.record} />
        <BoardSlot record={work.record} />
        <VideoSlotCell work={work} />
      </Link>
    </li>
  );
}

function Pages({ page, count, onPick }: { page: number; count: number; onPick: () => void }) {
  const move = (to: number, label: string, text: string) => (
    <Link to={pageHref(to)} onClick={onPick} className="btn-ghost text-xs" aria-label={label}>{text}</Link>
  );
  return (
    <nav className="flex flex-wrap items-center justify-center gap-2" aria-label="제작 기록 페이지">
      {page > 1 && move(page - 1, "이전 페이지", "‹")}
      {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
        <Link key={n} to={pageHref(n)} onClick={onPick} aria-label={`${n}페이지`} aria-current={n === page ? "page" : undefined}
          className={`min-w-9 text-xs tabular-nums ${n === page ? "btn-primary" : "btn-ghost"}`}>{n}</Link>
      ))}
      {page < count && move(page + 1, "다음 페이지", "›")}
    </nav>
  );
}

const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

function WorkList() {
  // 두 목록을 따로 읽는다: 한쪽이 막혀도 다른 쪽은 보여 준다
  const [records, setRecords] = useState<CreativeRecordSummary[] | null>(null);
  const [videos, setVideos] = useState<RequestVideo[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    const fail = (text: string) => { if (live) setErrors((prev) => [...prev.filter((p) => p !== text), text]); };
    listCreativeRecords(100).then((items) => live && setRecords(items), (e: unknown) => {
      fail(message(e, "제작 기록을 읽지 못했어요"));
      if (live) setRecords([]);
    });
    listRequestVideos(null, 100).then((items) => live && setVideos(items), (e: unknown) => {
      fail(message(e, "의뢰로 만든 영상을 읽지 못했어요"));
      if (live) setVideos([]);
    });
    return () => { live = false; };
  }, []);

  const works = records && videos ? mergeWorks(records, videos) : null;
  // 읽을 때 서버가 승인 대기를 다시 확인한다. 못 읽으면 칩과 개수를 보이지 않는다
  const promo = usePromoSite().status;
  const [params] = useSearchParams();
  const page = pageOf(params.get("page"), works?.length ?? 0);
  const count = pageCount(works?.length ?? 0);
  const top = useRef<HTMLDivElement>(null);
  const toTop = () => top.current?.scrollIntoView?.({ block: "start" });
  return (
    <div ref={top} className="space-y-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">제작 기록</h1>
          <p className="mt-1 text-sm text-ink-500">이미지 시트부터 완성 영상까지 작업 한 건씩 보여 줘요. 올리기는 늘 사람 승인이고, 기록은 최근 200건까지 남아요.</p>
        </div>
        {promo?.available && <span className="ml-auto text-xs text-ink-500">홍보 사이트 {promo.used}/{promo.limit}개</span>}
        <Link to="/marketing/sns" className={`btn-ghost text-xs ${promo?.available ? "" : "ml-auto"}`}>새로 만들기 ›</Link>
      </header>
      {errors.map((e) => <p key={e} className="text-sm text-accent" role="alert">{e}</p>)}
      <section className="card overflow-hidden" aria-label="제작 기록 목록">
        {works === null ? <p className="p-6 text-sm text-ink-500">불러오는 중…</p>
          : works.length === 0 ? <p className="p-6 text-center text-sm text-ink-500">{errors.length ? "보여 줄 기록이 없어요." : "아직 기록이 없어요. SNS·미디어에서 이미지 시트를 만들거나, 총괄에 홍보 영상을 의뢰하면 여기에 쌓여요."}</p>
            : (
              <>
                <div className={`${COLUMNS} border-b border-ink-100 py-2 text-[11px] text-ink-500 sm:text-xs`} aria-hidden="true">
                  <span>작업</span>
                  <span className="text-center"><span className="hidden sm:inline">이미지 </span>시트</span>
                  <span className="text-center"><span className="hidden sm:inline">대본·스토리보드</span><span className="sm:hidden">콘티</span></span>
                  <span className="text-center">영상</span>
                </div>
                {groupByDay(pageItems(works, page)).map((group) => (
                  <div key={group.day}>
                    <h2 className="bg-surface px-3 py-2 text-xs font-semibold text-ink-700 sm:px-4">{group.day}</h2>
                    <ul className="divide-y divide-ink-100" aria-label={`${group.day} 작업`}>
                      {group.works.map((w) => <Row key={w.key} work={w} page={page} promo={promo} />)}
                    </ul>
                  </div>
                ))}
              </>
            )}
      </section>
      {count > 1 && <Pages page={page} count={count} onPick={toTop} />}
    </div>
  );
}

export function CreativeRecords() {
  const [params] = useSearchParams();
  const id = params.get("id");
  if (id && RECORD_ID.test(id)) return <RecordDetail key={id} recordId={id} />;
  const requestId = requestFilterOf(params.get("request"));
  if (requestId) return <RequestDetail key={requestId} requestId={requestId} />;
  return <WorkList />;
}
