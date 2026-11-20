// "PDF로 저장"(window.print) 공용. 누른 동안만 body에 클래스를 붙여 styles.css @media print가 대상만 남긴다.
// - 보고서: print-trend-report + .trend-report-print (components/data/TrendReportView.tsx)
// - 기획서·기획 제안: print-doc + .doc-print (components/planning/specs.tsx · proposals.tsx)
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { PRINT_QUERY } from "./planDoc";

export const PRINT_DOC_CLASS = "print-doc";

/** body에 cls를 붙이고 인쇄, 인쇄 창이 닫히면(afterprint) 뗀다 */
export function printWithClass(cls: string) {
  const body = document.body;
  body.classList.add(cls);
  const done = () => { body.classList.remove(cls); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}

export const printDoc = () => printWithClass(PRINT_DOC_CLASS);

/** 인쇄 중인지 (beforeprint~afterprint). 접힌 칸을 인쇄 때 펼치는 데 쓴다 */
export function usePrinting(): boolean {
  const [p, setP] = useState(false);
  useEffect(() => {
    const on = () => setP(true), off = () => setP(false);
    window.addEventListener("beforeprint", on);
    window.addEventListener("afterprint", off);
    return () => { window.removeEventListener("beforeprint", on); window.removeEventListener("afterprint", off); };
  }, []);
  return p;
}

/** 주소에 ?print=1이 있으면 화면이 그려진 뒤 한 번 인쇄하고 print를 주소에서 지운다(replace).
 *  대화 카드 "PDF로 저장"이 기획서·제안 화면을 이렇게 연다 (보고서 카드의 autoPrint와 같은 규칙, 기획 화면은 쿼리로) */
export function useAutoPrintQuery(ready: boolean) {
  const [params, setParams] = useSearchParams();
  const want = params.get(PRINT_QUERY) === "1";
  const fired = useRef(false);
  useEffect(() => {
    if (!want || !ready || fired.current) return;
    fired.current = true;
    const next = new URLSearchParams(params);
    next.delete(PRINT_QUERY);
    setParams(next, { replace: true });
    // 주소를 바꾼 뒤 다시 그려져도 인쇄는 한 번만 (정리 함수로 지우면 replace 뒤에 인쇄가 취소된다)
    setTimeout(printDoc, 0);
  }, [want, ready, params, setParams]);
  useEffect(() => { if (!want) fired.current = false; }, [want]);
}
