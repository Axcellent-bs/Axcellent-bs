// 시스템 관리 메뉴 (AdminShell). 시스템 관리 탭은 모두에게 보이고, 관리자가 아니면 그 아래에 도구 연결만 보인다.
// 도구 연결 안에서도 회사 공용 연결(형상관리·배포·문서)은 관리자만 바꾸고, 일반 회원은 상태만 본다 (ToolConnections.tsx).
// 화면 없는 함수만 둔다 (node --test로 시험: systemMenu.test.ts).
import type { SubItem } from "./services";

export const TOOLS_HREF = "/system/tools";

export const SYSTEM_MENU: readonly SubItem[] = [
  { href: "/system/connect", label: "회사 연결" },
  { href: "/system/services", label: "서비스 고르기" },
  { href: "/system/members", label: "회원·서버 관리" },
  { href: TOOLS_HREF, label: "도구 연결" },
];

/** 이 계정이 시스템 관리 아래에서 보는 메뉴: 관리자는 넷 다, 아니면 도구 연결만 */
export const systemMenuFor = (admin: boolean): SubItem[] =>
  admin ? [...SYSTEM_MENU] : SYSTEM_MENU.filter((m) => m.href === TOOLS_HREF);

/** 회사 연결 전 관리자 메뉴: 회사 연결만 */
export const preConnectMenu = (): SubItem[] => SYSTEM_MENU.slice(0, 1);
