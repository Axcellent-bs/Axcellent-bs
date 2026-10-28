import { useState, type FormEvent, type ReactNode } from "react";
import { ADMIN_DEMO_PASSWORD, ADMIN_LOGIN } from "@/lib/env";

const KEY = "ax-admin-demo-login";

const remembered = () => {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
};

/**
 * 관리자 데모 로그인. `VITE_ADMIN_LOGIN=on`일 때만 비밀번호 입력이 앞에 선다.
 * 실제 인증이 아니다: 비밀번호가 번들에 들어가고, API는 사람 토큰(ax-backend #295)이 따로 막는다.
 */
export function DemoGate({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState(() => !ADMIN_LOGIN || remembered());
  const [value, setValue] = useState("");
  const [wrong, setWrong] = useState(false);

  if (ok) return <>{children}</>;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value === ADMIN_DEMO_PASSWORD) {
      try {
        sessionStorage.setItem(KEY, "1");
      } catch {
        /* 저장할 수 없어도 이번 접속은 통과한다 */
      }
      setOk(true);
    } else {
      setWrong(true);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
        <h1 className="text-lg font-semibold text-ink-900">캣북스 관리자</h1>
        <p className="text-sm text-ink-600">데모 비밀번호를 입력하세요.</p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setWrong(false);
          }}
          aria-label="데모 비밀번호"
          aria-invalid={wrong}
          className="w-full rounded-lg border border-ink-100 px-3 py-2 text-sm"
        />
        {wrong && <p role="alert" className="text-sm text-red-600">비밀번호가 맞지 않습니다.</p>}
        <button type="submit" className="w-full rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white">
          들어가기
        </button>
      </form>
    </main>
  );
}
