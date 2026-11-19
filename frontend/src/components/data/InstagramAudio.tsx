import { Fragment, useState } from "react";
import { ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { IgWhyFacts } from "@/components/data/IgWhyFacts";
import { SummaryBody, SummaryCard } from "@/components/data/KeywordTrend";
import { when } from "@/components/data/Sources";
import { useSkill, type SkillState } from "@/components/data/useSkill";
import { safeLink } from "@/lib/safeLink";
import { dataAi, type InstagramAudioItem, type InstagramAudioOutput, type InstagramAudioType } from "@/lib/data";

// 트렌드 1 감지 "인스타" 칩의 인기 오디오 (instagram_audio). 서버가 미리 계산한 목록을 읽기만 한다.
// - 음악 / 오리지널 토글 (audio_type music / original_sound). 음악 목록은 부모(PlatformViews)가 한 번 받아 종합 요약 칸과 같이 쓴다
// - 제목·아티스트는 외부 글이라 텍스트로만. 링크는 서버가 준 link(https)만. 썸네일은 걸지 않는다
// - 행을 누르면 "왜 뜨나" 근거 표(why_facts: 7일 전 순위·유튜브 쇼츠·네이버·확인 불가, 서버 코드 값)를 펼친다. 결론 문장은 없다
// - 키가 없으면(igKeyMissing) 부모가 이 카드 대신 "키 등록 후 가능" 한 줄을 그린다

export const KEY_LATER = "키 등록 후 가능";

const TYPES: { key: InstagramAudioType; label: string }[] = [
  { key: "music", label: "음악" },
  { key: "original_sound", label: "오리지널" },
];

/** 125000 → "2:05" */
function duration(ms: number | null): string | null {
  if (ms === null) return null;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 토큰 자체가 없어 인스타를 못 읽는 상태면 서버 이유, 아니면 null.
 *  출처 ig_audio와 ig_business_discovery가 둘 다 key_missing이거나, 요청 실패 이유에 "키 등록 후 가능"이 있을 때만.
 *  권한 부족(permission)은 키 없음이 아니다: 그때는 카드마다 자기 이유를 그린다 */
export function igKeyMissing(s: SkillState<InstagramAudioOutput>): string | null {
  if (s.kind === "failed") return s.reason.includes(KEY_LATER) ? s.reason : null;
  if (s.kind !== "ok") return null;
  const missing = (id: string) => s.out.sources.some((x) => x.source_id === id && x.status === "key_missing");
  if (!missing("ig_audio") || !missing("ig_business_discovery")) return null;
  const reasons = s.out.sources.filter((x) => x.status === "key_missing" && x.reason).map((x) => `${x.name}: ${x.reason}`);
  return [...new Set([s.out.reason, ...reasons].filter(Boolean))].join(" · ");
}

/** 서버 이유의 앞부분만 ("권한 필요: 인기 오디오는 …" → "권한 필요"). 요약 칸처럼 좁은 곳에 쓴다 */
export const shortReason = (reason: string | null | undefined): string | null => {
  if (!reason) return null;
  const head = reason.split(/[:：(]/)[0].trim();
  return head || reason;
};

/** 어제 대비: ▲n(초록) / ▼n / 새로 / - */
function Change({ i }: { i: InstagramAudioItem }) {
  if (i.is_new) return <span className={`chip text-xs ${TONE.info}`}>새로</span>;
  if (i.rank_change === null || i.rank_change === 0) return <span className="text-ink-500">-</span>;
  return i.rank_change > 0
    ? <span className="font-semibold text-emerald-700">▲{i.rank_change}</span>
    : <span className="text-ink-700">▼{-i.rank_change}</span>;
}

function AudioTable({ out }: { out: InstagramAudioOutput }) {
  // 행을 누르면 "왜 뜨나" 근거(why_facts)를 펼친다 (곡마다 따로)
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  if (out.items.length === 0) {
    return <p className="text-sm text-ink-700">{out.reason ?? "받은 오디오가 없어요."}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">순위</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">오디오</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 text-right font-medium">어제</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 text-right font-medium">며칠째</th>
          </tr>
        </thead>
        <tbody>
          {out.items.map((i) => {
            const title = i.title || i.audio_id;
            const link = safeLink(i.link);
            const sub = [duration(i.duration_ms), i.artist].filter(Boolean).join(" · ");
            const facts = i.why_facts ?? [];
            const shown = open.has(i.audio_id);
            return (
              <Fragment key={i.audio_id}>
                <tr className={`align-top ${facts.length ? "cursor-pointer hover:bg-ink-50" : ""} ${shown ? "" : "border-b border-ink-100"}`}
                  onClick={facts.length ? () => toggle(i.audio_id) : undefined}>
                  <td className="w-10 px-1 py-2 font-semibold tabular-nums text-ink-700">{i.rank}</td>
                  <td className="min-w-[12rem] px-1 py-2">
                    {link ? (
                      <a href={link} target="_blank" rel="noreferrer noopener" onClick={(e) => e.stopPropagation()}
                        className="font-semibold text-ink-900 underline-offset-2 hover:underline">{title}</a>
                    ) : <span className="font-semibold text-ink-900">{title}</span>}
                    {sub && <small className="block text-xs text-ink-500">{sub}</small>}
                    {i.overlap.length > 0 && <small className="block text-xs text-ink-500">{i.overlap.join(", ")} 와 겹침</small>}
                    {facts.length > 0 && (
                      <button type="button" aria-expanded={shown} onClick={(e) => { e.stopPropagation(); toggle(i.audio_id); }}
                        className="mt-0.5 text-[11px] text-brand-700 underline-offset-2 hover:underline">
                        왜 뜨나 {shown ? "접기 ▴" : "보기 ▾"}
                      </button>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums"><Change i={i} /></td>
                  <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">{i.streak_days}일</td>
                </tr>
                {shown && (
                  <tr className="border-b border-ink-100">
                    <td colSpan={4} className="px-1 pb-2"><IgWhyFacts facts={facts} /></td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AudioBody({ s, onRetry }: { s: SkillState<InstagramAudioOutput>; onRetry?: () => void }) {
  if (s.kind === "loading") return <Waiting what="인기 오디오를 받는 중…" />;
  if (s.kind === "failed") return <ErrorBox message={s.reason} onRetry={onRetry} />;
  const out = s.out;
  return (
    <div className="space-y-2">
      {out.status === "unknown" && out.items.length > 0 && out.reason && <p className="text-xs text-amber-700">{out.reason}</p>}
      <AudioTable out={out} />
      <p className="text-xs text-ink-500">
        {[out.day ? `${out.day} 목록` : null, out.previous_day ? `어제 = ${out.previous_day}` : null, out.generated_at ? `계산 ${when(out.generated_at)}` : null].filter(Boolean).join(" · ")}
      </p>
      <p className="text-xs text-ink-500">{out.limitation}{out.overlap_reason ? ` · ${out.overlap_reason}` : ""}</p>
    </div>
  );
}

/** 오리지널 사운드는 토글을 눌렀을 때만 받는다 */
function OriginalAudio() {
  const res = useSkill(() => dataAi.instagramAudio("original_sound"));
  return <AudioBody s={res.state} onRetry={res.reload} />;
}

/** music: 부모가 받은 음악 목록 (종합 요약 칸과 같이 쓴다) */
export function InstagramAudio({ music, onRetryMusic }: { music: SkillState<InstagramAudioOutput>; onRetryMusic?: () => void }) {
  const [type, setType] = useState<InstagramAudioType>("music");
  const tools = (
    <>
      <span className="text-xs text-ink-500">IG Audio API</span>
      <div role="group" aria-label="오디오 종류" className="flex gap-1">
        {TYPES.map((t) => (
          <button key={t.key} type="button" aria-pressed={type === t.key} onClick={() => setType(t.key)}
            className={`rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${type === t.key ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`}>
            {t.label}
          </button>
        ))}
      </div>
    </>
  );
  return (
    <Panel id="data-trend-ig-audio" title="인기 오디오" tools={tools}>
      {type === "music" ? <AudioBody s={music} onRetry={onRetryMusic} /> : <OriginalAudio />}
    </Panel>
  );
}

/** 종합 칩 요약 줄의 "인스타 인기 오디오 1위" 칸 (부모가 받은 음악 목록) */
export function IgAudioSummaryCard({ state }: { state: SkillState<InstagramAudioOutput> }) {
  const label = "인스타 인기 오디오 1위";
  const keyMissing = igKeyMissing(state);
  if (state.kind !== "ok" || keyMissing !== null) {
    const note = state.kind === "loading" ? "받는 중…" : keyMissing !== null ? KEY_LATER : "받지 못했어요";
    return <SummaryCard label={label}><p className="text-sm text-ink-500">{note}</p></SummaryCard>;
  }
  const top = state.out.items[0] ?? null;
  // 모르면(unknown) 값 대신 짧은 이유 (예: "권한 필요"). 전체 이유는 title로
  if (state.out.status === "unknown" || !top) {
    const reason = state.out.reason;
    return (
      <SummaryCard label={label}>
        <p className="text-sm text-ink-500" title={reason ?? undefined}>{shortReason(reason) ?? "받은 오디오가 없어요."}</p>
      </SummaryCard>
    );
  }
  const change = top.is_new ? "새로 들어옴"
    : top.rank_change === null ? null
    : top.rank_change > 0 ? `어제보다 ▲${top.rank_change}`
    : top.rank_change < 0 ? `어제보다 ▼${-top.rank_change}`
    : "어제와 같은 순위";
  return (
    <SummaryCard label={label}>
      <SummaryBody title={top.title || top.audio_id} lines={[`${top.streak_days}일째`, change]} />
    </SummaryCard>
  );
}
