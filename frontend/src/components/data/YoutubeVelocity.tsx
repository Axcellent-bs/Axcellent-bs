import { useEffect, useState } from "react";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { DATA_SERVER, UNKNOWN, dataAi, kpiLive, type YouTubeVelocityOutput, type YouTubeVideoVelocity } from "@/lib/data";

// 트렌드 1단계 아래 "YouTube 지금 빨리 크는 영상": 데이터 AI(8106) youtube_velocity 출력을 그대로 보여 준다.
// - 판정하지 않는다(평소 속도 기준이 아직 없음). "급상승" 같은 판정 말을 쓰지 않고 지금 속도 큰 순서만 보여 준다
// - 지금 속도 = 최근 두 스냅샷 사이 시간당 조회 증가. 빨라졌는지는 그 전 구간과 비교(서버 값)
// - 영상은 제목(+채널)으로 보여 준다. 제목이 없는 예전 응답은 영상 ID
// - 숫자 칸이 빠진 영상(좋아요 숨김 등)은 서버가 비워 두고 화면은 "-"로 보여 준다(0 아님)
// - 스냅샷 DB만 읽어서 외부 호출이 없다. 트렌드 탭을 열 때 한 번 + "다시 보기"

const num = (v: number | null | undefined, digits = 0) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : "-";
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : "-");

function Row({ v, rank }: { v: YouTubeVideoVelocity; rank: number }) {
  return (
    <li className="flex flex-wrap items-start gap-3 py-3">
      <span className="w-6 text-sm font-semibold text-ink-500">{rank}</span>
      <div className="min-w-0 flex-1 space-y-1">
        <a className="font-semibold text-brand-700 underline-offset-2 hover:underline" href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`}
          target="_blank" rel="noreferrer noopener">
          {v.title || `영상 ${v.video_id}`}
        </a>
        {v.channel_title && <p className="text-xs text-ink-700">{v.channel_title}</p>}
        <p className="text-xs text-ink-500">
          조회 {num(v.views)} · 좋아요 {num(v.likes)} · 댓글 {num(v.comments)}
          {v.popular_rank != null ? ` · 인기 목록 ${v.popular_rank}위` : ""}
        </p>
        {v.tags.length > 0 && <p className="truncate text-[11px] text-ink-500">태그: {v.tags.slice(0, 6).join(", ")}</p>}
      </div>
      <div className="text-right">
        <p className="text-sm font-bold text-ink-900">시간당 조회 +{num(v.views_per_hour)}</p>
        {v.faster != null && (
          <p className={`text-xs ${v.faster ? "text-red-700" : "text-ink-500"}`}>
            그 전 구간({num(v.prev_views_per_hour)})보다 {v.faster ? "빨라졌어요" : "느려졌어요"}
          </p>
        )}
      </div>
    </li>
  );
}

type State = { kind: "loading" } | { kind: "ok"; out: YouTubeVelocityOutput } | { kind: "failed"; reason: string };

export function YoutubeVelocity() {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.youtubeVelocity()
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);
  if (!kpiLive) return null;
  const out = state.kind === "ok" ? state.out : null;
  return (
    <Panel id="data-trend-youtube-velocity" title="YouTube 지금 빨리 크는 영상"
      hint={out?.status === "known" ? `스냅샷 ${when(out.latest_at)} (그 전 ${when(out.previous_at)}) 사이 시간당 조회 증가` : undefined}
      tools={<>
        <Chip label="판정 없음" tone={TONE.muted} />
        <button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 보기</button>
      </>}>
      {state.kind === "loading" && <Waiting />}
      {state.kind === "failed" && <ErrorBox message={`${state.reason}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={() => setRun((n) => n + 1)} />}
      {out && out.status === "unknown" && <Empty>{UNKNOWN} · {out.unknown_reason}</Empty>}
      {out && out.status === "known" && (
        out.videos.length === 0 ? <Empty>지금 속도를 잴 수 있는 영상이 없어요.</Empty> : (
          <>
            <p className="text-sm text-ink-700">평소 속도와 비교한 판정은 아직 없어요. 최근 두 스냅샷 사이에 조회가 빨리 늘어난 순서만 보여 줘요.</p>
            <ol className="divide-y divide-ink-100">
              {out.videos.map((v, i) => <Row key={v.video_id} v={v} rank={i + 1} />)}
            </ol>
            <p className="mt-2 text-xs text-ink-500">{out.summary}</p>
          </>
        )
      )}
    </Panel>
  );
}
