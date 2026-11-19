import { useEffect, useState } from "react";
import { dashboardAuth } from "./env";

// dashboard-api의 미디어 미리보기(이미지·영상)를 사람 토큰을 붙여 받아 브라우저 안 주소(blob:)로 바꾼다.
// 서버는 읽기에도 사람 토큰을 요구하는데 img·video의 src와 a의 href는 Authorization 헤더를
// 붙이지 못해서, 주소를 그대로 넣으면 401로 깨진다. 받은 주소는 화면을 떠나거나 주소가 바뀌면 놓아 준다.

export interface AuthedMedia {
  /** 그릴 주소. 받는 중이거나 실패하면 null */
  src: string | null;
  /** 받지 못했다(401·404·연결 실패 등) */
  failed: boolean;
}

export function useAuthedMedia(url: string | null): AuthedMedia {
  const [state, setState] = useState<{ url: string | null; src: string | null; failed: boolean }>({ url: null, src: null, failed: false });
  useEffect(() => {
    if (!url) return;
    let alive = true;
    let objectUrl: string | null = null;
    const ctrl = new AbortController();
    fetch(url, { headers: dashboardAuth(), cache: "no-store", signal: ctrl.signal })
      .then((res) => (res.ok ? res.blob() : Promise.reject(new Error("HTTP " + res.status))))
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url, src: objectUrl, failed: false });
      })
      .catch(() => {
        if (alive) setState({ url, src: null, failed: true });
      });
    return () => {
      alive = false;
      ctrl.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  // 주소가 바뀐 직후에는 지난 주소의 결과를 보이지 않는다
  return url && state.url === url ? { src: state.src, failed: state.failed } : { src: null, failed: false };
}
