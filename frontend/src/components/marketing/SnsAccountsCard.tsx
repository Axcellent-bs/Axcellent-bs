import { useCallback, useEffect, useState } from "react";
import { KEY_LABEL, keyHintKo, keyResultKo, readYoutubeReturn, type CompanyAiAdmin, type KeyName } from "@/lib/companyAi";
import { deleteCompanyAiKey, errText, getCompanyAi, putCompanyAiKey, youtubeComplete, youtubeConnectUrl, youtubeDisconnect } from "@/lib/companyAiApi";

// 마케팅 › SNS·미디어 맨 위. 회사마다 자기 인스타·유튜브 계정을 연결한다(로그인한 회사 기준, 11/23).
// - 값은 그 회사의 키 저장소에만 들어간다(tenant=회사 ID). 화면에는 끝 4자리만 보인다
// - 유튜브: [유튜브 채널 연결] → 구글 동의 화면(채널 주인 계정) → 돌아오면 ?youtube=finish#코드 →
//   이 화면이 로그인한 계정으로 complete를 부른다(연결을 시작한 계정·회사일 때만 서버가 넣는다)
// - 인스타: Meta에서 받은 Instagram 토큰·계정 ID를 관리자가 넣는다
// - 바꾸기는 고객사 관리자만. 관리자가 아니면 안내만 보인다

const IG_TOKEN: KeyName = "MARKETING_IG_ACCESS_TOKEN";
const IG_USER: KeyName = "MARKETING_IG_USER_ID";
const YT: KeyName = "MARKETING_YOUTUBE_REFRESH_TOKEN";
const BACK_KO: Record<string, string> = {
  connected: "유튜브 채널을 연결했어요",
  denied: "구글 동의를 취소해서 연결하지 않았어요",
  bad_state: "연결 시간이 지났거나 주소가 맞지 않아요. 다시 눌러 주세요",
  app_missing: "서버에 AXcellent 유튜브 앱 설정이 없어요",
  google_failed: "구글에 닿지 않았어요. 잠시 뒤 다시 해 주세요",
  no_refresh_token: "구글이 연결 값을 주지 않았어요. 구글 계정의 앱 권한에서 AXcellent를 지우고 다시 연결해 주세요",
  store_failed: "키 저장소에 넣지 못했어요(키 저장소가 꺼져 있을 수 있어요)",
};

function Dot({ on }: { on: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${on ? "bg-emerald-500" : "bg-ink-300"}`} aria-hidden="true" />;
}

export function SnsAccountsCard() {
  const [admin, setAdmin] = useState<CompanyAiAdmin | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");
  const [userId, setUserId] = useState("");

  const load = useCallback(async () => {
    try {
      setAdmin(await getCompanyAi());
      setDenied(null);
    } catch (e) {
      setDenied(errText(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const back = new URLSearchParams(window.location.search).get("youtube");
    if (back !== "finish") {
      if (back) setMsg(BACK_KO[back] ?? "유튜브 연결을 마치지 못했어요");
      return;
    }
    // 구글에서 돌아왔다: 코드는 주소의 # 뒤에만 있다. 주소에서 바로 지우고, 로그인한 이 계정으로 마친다
    const got = readYoutubeReturn(window.location.hash);
    const url = new URL(window.location.href);
    url.hash = "";
    url.searchParams.delete("youtube");
    window.history.replaceState(null, "", url.toString());
    if (!got) {
      setMsg(BACK_KO.bad_state);
      return;
    }
    void run(async () => {
      await youtubeComplete(got.code, got.state);
      return BACK_KO.connected;
    });
  }, [load]); // 돌아온 순간 한 번만 (run은 매 렌더 새로 만들어져 넣지 않는다)

  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true);
    setMsg(null);
    try {
      const out = await fn();
      if (out) setMsg(out);
      await load();
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const row = (name: KeyName) => admin?.keys[name];
  const ytOn = !!row(YT)?.has_key;
  const igOn = !!row(IG_TOKEN)?.has_key && !!row(IG_USER)?.has_key;
  const admins = admin?.approval.admins ?? 1;
  const blocked = !admin?.secret_store.enabled;

  const saveIg = async () => {
    const parts: string[] = [];
    if (token.trim()) parts.push(`${KEY_LABEL[IG_TOKEN]}: ${keyResultKo(await putCompanyAiKey(IG_TOKEN, token.trim()), admins)}`);
    if (userId.trim()) parts.push(`${KEY_LABEL[IG_USER]}: ${keyResultKo(await putCompanyAiKey(IG_USER, userId.trim()), admins)}`);
    setToken("");
    setUserId("");
    return parts.join(" ");
  };
  const dropIg = async () => {
    await deleteCompanyAiKey(IG_TOKEN, "인스타 연결 끊기");
    await deleteCompanyAiKey(IG_USER, "인스타 연결 끊기");
    return "인스타 연결 끊기를 요청했어요";
  };

  return (
    <section className="card space-y-4 px-6 py-5" aria-labelledby="sns-accounts-title">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="sns-accounts-title" className="text-sm font-semibold">SNS 계정 연결</h2>
        {admin?.company && <span className="chip bg-ink-50 text-ink-700">{admin.company.name}</span>}
        <span className="text-xs text-ink-500">회사마다 자기 인스타·유튜브 계정을 써요. 로그인한 회사 계정으로 올라가요</span>
      </div>
      {denied ? (
        <p className="text-sm text-ink-700">연결을 바꾸는 건 고객사 관리자만 할 수 있어요. ({denied})</p>
      ) : !admin ? (
        <p className="text-sm text-ink-500">확인하는 중…</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2 rounded-xl border border-ink-100 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-ink-900"><Dot on={ytOn} />유튜브 {ytOn ? "연결됨" : "연결 전"}</div>
            <p className="text-xs text-ink-500">채널 주인 구글 계정으로 동의하면 이 회사 채널에 영상을 올릴 수 있어요. 올리기는 늘 사람 승인을 거쳐요</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary text-xs" disabled={busy || blocked}
                onClick={() => void run(async () => { window.location.assign(await youtubeConnectUrl()); })}>
                {ytOn ? "다른 채널로 다시 연결" : "유튜브 채널 연결"}
              </button>
              {ytOn && (
                <button type="button" className="btn-ghost text-xs" disabled={busy}
                  onClick={() => void run(async () => { await youtubeDisconnect(); return "유튜브 연결을 끊었어요"; })}>연결 끊기</button>
              )}
            </div>
          </div>
          <div className="space-y-2 rounded-xl border border-ink-100 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-ink-900"><Dot on={igOn} />인스타그램 {igOn ? "연결됨" : "연결 전"}</div>
            <p className="text-xs text-ink-500">
              Meta에서 받은 Instagram 토큰과 계정 ID를 넣어요. 지금 {KEY_LABEL[IG_TOKEN]} {keyHintKo(row(IG_TOKEN)?.hint ?? "")} · {KEY_LABEL[IG_USER]} {keyHintKo(row(IG_USER)?.hint ?? "")}
            </p>
            <input className="field text-sm" type="password" autoComplete="off" aria-label="인스타 토큰" placeholder="인스타 토큰" value={token} onChange={(e) => setToken(e.target.value)} />
            <input className="field text-sm" autoComplete="off" aria-label="인스타 계정 ID" placeholder="인스타 계정 ID(숫자)" value={userId} onChange={(e) => setUserId(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary text-xs" disabled={busy || blocked || (!token.trim() && !userId.trim())}
                onClick={() => void run(saveIg)}>저장</button>
              {igOn && <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => void run(dropIg)}>연결 끊기</button>}
            </div>
          </div>
        </div>
      )}
      {blocked && admin && <p className="text-xs text-accent">{admin.secret_store.reason}</p>}
      {msg && <p className="text-xs text-ink-700" role="status">{msg}</p>}
    </section>
  );
}
