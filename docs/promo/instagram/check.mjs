// 인스타 자료(instagram.json)를 검사한다. 외부 패키지 없음.
// 사용: node check.mjs [파일 이름(기본 instagram.json)] [--no-files]
//   --no-files: 사진·영상 파일이 실제로 있는지는 보지 않고 형식만 본다(example.json 확인용)
// 오류가 있으면 종료 코드 1. 규칙은 README.md 참고.
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TYPES = new Set(["image", "video", "carousel"]);
const KINDS = new Set(["real", "team"]);
const IMG_EXT = /\.(jpe?g|png|webp)$/;
const VID_EXT = /\.mp4$/;
const NAME_OK = /^[a-z0-9][a-z0-9\-_./]*$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const IG = /^https:\/\/(www\.)?instagram\.com\//;
const MAX_IMG = 2 * 1024 * 1024;
const MAX_VID = 15 * 1024 * 1024;

const isStr = (v) => typeof v === "string" && v.trim() !== "";
const isDate = (v) => typeof v === "string" && ISO.test(v) && !Number.isNaN(Date.parse(v));
const today = () => new Date().toISOString().slice(0, 10);

export function validate(data, { baseDir = HERE, checkFiles = true } = {}) {
  const errors = [];
  const warnings = [];
  const err = (where, msg) => errors.push(`${where}: ${msg}`);
  const warn = (where, msg) => warnings.push(`${where}: ${msg}`);

  // 파일 하나(경로, 허용 종류 image|video|any)
  function file(where, p, kind) {
    if (!isStr(p)) return err(where, "파일 경로가 비어 있다");
    if (!NAME_OK.test(p) || p.includes("..")) return err(where, `파일 이름은 영문 소문자·숫자·-·_만 쓴다: ${p}`);
    if (!p.startsWith("assets/")) return err(where, `파일은 assets/ 아래에 둔다: ${p}`);
    const isImg = IMG_EXT.test(p);
    const isVid = VID_EXT.test(p);
    if (kind === "image" && !isImg) return err(where, `이미지(jpg·png·webp)가 아니다: ${p}`);
    if (kind === "video" && !isVid) return err(where, `영상(mp4)이 아니다: ${p}`);
    if (kind === "any" && !isImg && !isVid) return err(where, `jpg·png·webp·mp4만 쓴다: ${p}`);
    if (!checkFiles) return;
    // instagram/ 아래에 없으면 홍보 사이트 공용 폴더(docs/promo/assets/)에서 찾는다.
    // 자동 배포는 새 파일을 공용 폴더에서만 복사하므로 새 게시물 파일은 assets/instagram/… 에 둔다
    const own = path.join(baseDir, p);
    const full = existsSync(own) ? own : path.join(baseDir, "..", p);
    if (!existsSync(full)) return err(where, `파일이 없다: ${p}`);
    const size = statSync(full).size;
    if (isImg && size > MAX_IMG) err(where, `이미지는 2MB 이하여야 한다(${(size / 1048576).toFixed(1)}MB): ${p}`);
    if (isVid && size > MAX_VID) err(where, `영상은 15MB 이하여야 한다(${(size / 1048576).toFixed(1)}MB): ${p}`);
  }

  if (!data || typeof data !== "object") {
    err("전체", "JSON 객체가 아니다");
    return { errors, warnings };
  }
  if (data.version !== 1) err("version", "1이어야 한다");
  if (!Array.isArray(data.customers)) {
    err("customers", "목록이 아니다");
    return { errors, warnings };
  }

  const customerIds = new Set();
  data.customers.forEach((c, ci) => {
    const cw = `고객사[${ci}] ${c && c.name ? c.name : ""}`.trim();
    if (!c || typeof c !== "object") return err(cw, "객체가 아니다");
    if (!isStr(c.id) || !NAME_OK.test(c.id)) err(cw, "id는 영문 소문자·숫자·-·_");
    else if (customerIds.has(c.id)) err(cw, `id가 겹친다: ${c.id}`);
    else customerIds.add(c.id);
    if (!isStr(c.name)) err(cw, "name이 비어 있다");
    if (!Array.isArray(c.badges) || !c.badges.every(isStr)) err(cw, "badges는 글자 목록이어야 한다");
    else if (!c.badges.includes("시연 가상 고객사")) warn(cw, 'badges에 "시연 가상 고객사"가 없다');
    if (c.profileUrl !== "" && !(typeof c.profileUrl === "string" && IG.test(c.profileUrl))) err(cw, "profileUrl은 비우거나 https://www.instagram.com/… 이어야 한다");
    if (!Array.isArray(c.posts)) err(cw, "posts는 목록이어야 한다");
    if (!Array.isArray(c.highlights)) err(cw, "highlights는 목록이어야 한다");

    const postIds = new Set();
    (Array.isArray(c.posts) ? c.posts : []).forEach((p, pi) => {
      const w = `${cw} 게시물[${pi}] ${p && p.id ? p.id : ""}`.trim();
      if (!p || typeof p !== "object") return err(w, "객체가 아니다");
      if (!isStr(p.id) || !NAME_OK.test(p.id)) err(w, "id는 영문 소문자·숫자·-·_ (예: 2026-11-13-1)");
      else if (postIds.has(p.id)) err(w, `id가 겹친다: ${p.id}`);
      else postIds.add(p.id);
      if (!TYPES.has(p.type)) err(w, "type은 image·video·carousel 중 하나");
      file(`${w} img`, p.img, "image");
      if (!Array.isArray(p.media) || p.media.length === 0) err(w, "media(원본 파일 목록)가 비어 있다");
      else {
        p.media.forEach((m, mi) => file(`${w} media[${mi}]`, m, "any"));
        if (p.type === "video" && !VID_EXT.test(p.media[0] || "")) err(w, "video 게시물의 media 첫 번째는 mp4여야 한다");
        if (p.type === "carousel" && p.media.length < 2) warn(w, "carousel인데 media가 1개뿐이다");
      }
      if (!isStr(p.alt)) err(w, "alt(그림 설명)가 비어 있다");
      if (typeof p.caption !== "string") err(w, "caption은 글자여야 한다(없으면 빈 글자)");
      if (!isDate(p.date)) err(w, "date는 YYYY-MM-DD 형식이어야 한다");
      else if (p.date > today()) err(w, "아직 올리지 않은 게시물(미래 날짜)은 넣지 않는다");
      if (!(typeof p.permalink === "string" && IG.test(p.permalink))) err(w, "permalink(원본 주소)는 https://www.instagram.com/… 이어야 한다");
      if (typeof p.aiGenerated !== "boolean") err(w, "aiGenerated(AI가 만든 이미지·영상·글인가)는 true/false로 꼭 적는다");
      if (typeof p.axcellent !== "boolean") err(w, "axcellent(AXcellent를 간접 노출한 게시물인가)는 true/false로 꼭 적는다");
      if (p.axcellent === true && !(typeof p.caption === "string" && p.caption.includes("시연용"))) err(w, 'AXcellent를 노출한 게시물의 캡션에는 "시연용" 표기가 있어야 한다');

      const hasMetrics = Array.isArray(p.metrics) && p.metrics.length > 0;
      if (hasMetrics) {
        if (!p.metrics.every((m) => Array.isArray(m) && m.length === 2 && isStr(m[0]) && typeof m[1] === "string" && m[1].trim() !== "")) err(w, 'metrics는 [["도달","123"]] 꼴이어야 한다');
        if (!isDate(p.measuredAt)) err(w, "반응 숫자가 있으면 measuredAt(잰 날짜, YYYY-MM-DD)이 꼭 있어야 한다");
        else if (p.measuredAt > today()) err(w, "잰 날짜(measuredAt)가 미래다. 아직 잰 값이 아니다");
        else if (isDate(p.date) && p.measuredAt < p.date) err(w, "잰 날짜(measuredAt)가 게시 날짜보다 앞선다");
        if (!isStr(p.source)) err(w, "반응 숫자가 있으면 source(출처)가 꼭 있어야 한다");
        if (!KINDS.has(p.metricsKind)) err(w, "반응 숫자가 있으면 metricsKind(real=시연 계정에서 실제로 잰 값, team=팀 내 시연 반응)가 꼭 있어야 한다");
      } else if (p.measuredAt || p.source || p.metricsKind) {
        warn(w, "metrics가 비어 있는데 measuredAt·source·metricsKind가 있다(화면에는 안 나온다)");
      }
    });

    const hlIds = new Set();
    (Array.isArray(c.highlights) ? c.highlights : []).forEach((h, hi) => {
      const w = `${cw} 하이라이트[${hi}] ${h && h.title ? h.title : ""}`.trim();
      if (!h || typeof h !== "object") return err(w, "객체가 아니다");
      if (!isStr(h.id) || !NAME_OK.test(h.id)) err(w, "id는 영문 소문자·숫자·-·_");
      else if (hlIds.has(h.id)) err(w, `id가 겹친다: ${h.id}`);
      else hlIds.add(h.id);
      if (!isStr(h.title)) err(w, "title(하이라이트 이름)이 비어 있다");
      file(`${w} cover`, h.cover, "image");
      if (!Array.isArray(h.slides) || h.slides.length === 0) err(w, "slides(스토리 목록)가 비어 있다");
      else
        h.slides.forEach((s, si) => {
          const sw = `${w} 스토리[${si}]`;
          if (!s || typeof s !== "object") return err(sw, "객체가 아니다");
          if (s.type !== "image" && s.type !== "video") err(sw, "type은 image 또는 video");
          file(sw, s.src, s.type === "video" ? "video" : "image");
          if (s.date !== undefined && !isDate(s.date)) err(sw, "date는 YYYY-MM-DD 형식이어야 한다");
        });
    });
  });
  return { errors, warnings };
}

// 명령줄로 실행했을 때만 동작한다(다른 파일이 가져다 쓸 때는 실행하지 않는다)
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const checkFiles = !args.includes("--no-files");
  const name = args.find((a) => !a.startsWith("--")) || "instagram.json";
  const full = path.resolve(HERE, name);
  let data;
  try {
    data = JSON.parse(readFileSync(full, "utf8"));
  } catch (e) {
    console.error(`[오류] ${name}을 읽지 못했다: ${e.message}`);
    process.exit(1);
  }
  const { errors, warnings } = validate(data, { baseDir: HERE, checkFiles });
  warnings.forEach((w) => console.warn(`[경고] ${w}`));
  errors.forEach((e) => console.error(`[오류] ${e}`));
  const posts = data.customers?.reduce((n, c) => n + (c.posts?.length || 0), 0) ?? 0;
  const hls = data.customers?.reduce((n, c) => n + (c.highlights?.length || 0), 0) ?? 0;
  console.log(`${name}: 게시물 ${posts}개, 하이라이트 ${hls}개 — 오류 ${errors.length}건, 경고 ${warnings.length}건`);
  process.exit(errors.length ? 1 : 0);
}
