import { useEffect, useRef, useState } from "react";
import { mediumKo, mediumsFor, sourceKo, type CampaignLink } from "@/lib/adMetrics";
import { adMetricsApi } from "@/lib/adMetricsApi";

// 캠페인 추적 링크: 기본 주소에 UTM 꼬리표(utm_campaign·utm_source·utm_medium)를 붙인 링크를 마케팅 AI가 만든다(읽기).
// 기본 주소는 마케팅 설정값(응답 default_base_url)으로 채우고 화면에서 바꿀 수 있다. 코드에 주소를 두지 않는다.
// 조합표(source → medium)는 서버 응답에서 받는다. 클립보드가 막히면 링크를 선택해 두고 직접 복사하도록 안내한다.

export function TrackedLinkBox({ campaignId, combos, defaultBaseUrl }: {
  campaignId: string; combos: Record<string, string[]>; defaultBaseUrl: string | null;
}) {
  const [baseUrl, setBaseUrl] = useState(defaultBaseUrl ?? "");
  const [source, setSource] = useState("");
  const [medium, setMedium] = useState("");
  const [link, setLink] = useState<CampaignLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState<"done" | "failed" | null>(null);
  const output = useRef<HTMLInputElement>(null);

  // 캠페인이 바뀌면 만든 링크는 맞지 않아 지운다. 기본 주소가 늦게 오면 빈 칸만 채운다
  useEffect(() => { setLink(null); setCopied(null); setProblem(null); }, [campaignId]);
  useEffect(() => { if (defaultBaseUrl) setBaseUrl((v) => (v === "" ? defaultBaseUrl : v)); }, [defaultBaseUrl]);

  const mediums = mediumsFor(combos, source);
  const pickSource = (next: string) => {
    const options = mediumsFor(combos, next);
    setSource(next);
    setMedium(options.includes(medium) ? medium : options.length === 1 ? options[0] : "");
    setLink(null);
  };

  async function build() {
    setBusy(true);
    setProblem(null);
    setCopied(null);
    try {
      setLink(await adMetricsApi.link(campaignId, { base_url: baseUrl.trim(), source, medium }));
    } catch (e) {
      setLink(null);
      setProblem(`링크를 만들지 못했습니다: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  async function copy() {
    if (!link) return;
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(link.url);
      setCopied("done");
    } catch {
      output.current?.focus();
      output.current?.select();
      setCopied("failed");
    }
  }

  return (
    <div className="rounded-2xl border border-ink-100 p-4 text-sm">
      <h4 className="font-medium text-ink-900">추적 링크</h4>
      <p className="mt-0.5 text-xs text-ink-500">게시글·광고에 이 링크를 쓰면 들어온 방문을 캠페인·source·medium별로 셀 수 있습니다. 기본 주소는 마케팅 설정값이고 여기서 바꿀 수 있습니다.</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_160px_160px_auto] sm:items-end">
        <label className="block"><span className="text-ink-700">기본 주소</span>
          <input className="field mt-0.5 w-full" inputMode="url" placeholder="https://" value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setLink(null); }} />
        </label>
        <label className="block"><span className="text-ink-700">source</span>
          <select className="field mt-0.5 w-full" value={source} onChange={(e) => pickSource(e.target.value)}>
            <option value="">고르세요</option>
            {Object.keys(combos).map((s) => <option key={s} value={s}>{sourceKo(s)} ({s})</option>)}
          </select>
        </label>
        <label className="block"><span className="text-ink-700">medium</span>
          <select className="field mt-0.5 w-full" value={medium} disabled={mediums.length === 0} onChange={(e) => { setMedium(e.target.value); setLink(null); }}>
            <option value="">{source ? "고르세요" : "source 먼저"}</option>
            {mediums.map((m) => <option key={m} value={m}>{mediumKo(m)} ({m})</option>)}
          </select>
        </label>
        <button className="btn-ghost" disabled={busy || baseUrl.trim() === "" || !source || !medium} onClick={() => void build()}>{busy ? "만드는 중" : "링크 만들기"}</button>
      </div>
      {!defaultBaseUrl && <p className="mt-1 text-xs text-ink-500">마케팅 설정에 기본 주소가 없습니다. 쓸 주소를 직접 넣으세요.</p>}
      {problem && <p role="alert" className="mt-2 rounded-xl bg-red-50 px-3 py-2 text-red-800">{problem}</p>}
      {link && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input ref={output} className="field min-w-0 flex-1 font-mono text-xs" readOnly aria-label="꼬리표 붙은 링크" value={link.url} onFocus={(e) => e.target.select()} />
          <button className="btn-primary" onClick={() => void copy()}>복사</button>
        </div>
      )}
      {copied === "done" && <p role="status" className="mt-1 text-xs text-emerald-700">복사했습니다.</p>}
      {copied === "failed" && <p role="alert" className="mt-1 text-xs text-amber-800">브라우저가 클립보드를 막았습니다. 링크를 선택해 두었으니 Ctrl+C(맥은 Cmd+C)로 복사하세요.</p>}
    </div>
  );
}
