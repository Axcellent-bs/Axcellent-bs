# 인스타 자료 (캣북스·누비투어 게시물과 하이라이트)

홍보 웹페이지의 "콘텐츠" 칸(`posts.instagram`)에 보여 줄 **인스타그램 게시물·하이라이트 자료**를 모아 두는 곳이다. 자료는 준하가 만들고 갱신하고, 화면에 그리는 일은 병환이 한다.

상태: **캣북스 게시물 4개가 `instagram.json`에 들어 있다**(#1347): `2026-11-09-1`, `2026-11-12-1`, `2026-11-12-2`(릴스), `2026-11-13-1`. 4개 모두 `aiGenerated: true`, `axcellent: false`다. 누비투어·스터디온에듀는 아직 게시물이 없다. 게시물은 홍보 페이지의 **콘텐츠** 칸에 고객사별로 보인다(#1352, 이전에는 "고객사 마케팅 사례" 칸). 하이라이트는 계정 확인 전이라 비어 있다.

## 이 방식이 하는 일과 못 하는 일

- 하는 일: 캣북스(·누비투어) 게시물과 하이라이트를 **우리가 정리한 파일**로 홍보 페이지에 보여 주고, 각 게시물에서 원본 인스타(`permalink`)로 보낸다.
- 못 하는 일: **자동 최신화.** 새 게시물을 올리면 그때마다 사람이 이 폴더에 추가하고 페이지를 다시 만들어야 한다.
- 하이라이트는 인스타가 외부로 가져갈 방법(API·퍼가기)을 주지 않아서, 같은 모양으로 **다시 만들어** 둔다.
- 인스타 화면을 긁어 오거나 인스타 퍼가기 스크립트를 불러오지 않는다(약관, 방문자 정보 수집 방지).

## 폴더

| 파일 | 하는 일 |
| --- | --- |
| `instagram.json` | **진짜 자료.** 게시물·하이라이트를 여기에 적는다(지금은 캣북스 게시물 4개) |
| `assets/` | 사진·영상 파일. 고객사별 폴더를 쓴다(`assets/catbooks/…`) |
| `example.json` | 형식 예시. 값은 모두 지어낸 것이고 진짜 자료가 아니다 |
| `check.mjs` | 자료를 검사한다(아래 "검사") |
| `load.mjs` | 자료를 `content.mjs`가 바로 쓰는 모양으로 바꿔 준다(아래 "화면 쪽 연결") |

## 게시물 하나(`posts[]`)

| 칸 | 뜻 | 규칙 |
| --- | --- | --- |
| `id` | 게시물 이름 | 영문 소문자·숫자·`-`·`_`. 날짜-번호(`2026-11-13-1`)로 쓴다 |
| `type` | 종류 | `image` · `video` · `carousel`(여러 장) |
| `img` | 격자에 보일 대표 이미지(영상이면 첫 화면) | `assets/…` 아래 jpg·png·webp, 2MB 이하 |
| `media` | 원본 파일 목록 | 이미지·영상. `video`면 첫 번째가 mp4(15MB 이하) |
| `alt` | 그림 설명 | 비우지 않는다 |
| `caption` | 인스타에 쓴 글 | 줄바꿈은 `\n`. 없으면 빈 글자 |
| `date` | 올린 날 | `YYYY-MM-DD`. 아직 안 올린 게시물(미래 날짜)은 넣지 않는다 |
| `permalink` | 원본 인스타 주소 | `https://www.instagram.com/…` |
| `aiGenerated` | AI가 만든 이미지·영상·글인가 | `true`/`false` **꼭 적는다.** `true`면 화면에 "AI가 만든" 표기를 붙인다 |
| `axcellent` | AXcellent를 간접 노출한 게시물인가 | `true`/`false` **꼭 적는다.** `true`면 캡션에 `시연용` 표기가 있어야 한다(예: "이 게시물은 AXcellent AI 직원이 기획·제작했어요(시연용)") |
| `metrics` | 반응 숫자 | `[["도달","123"],["좋아요","12"]]`. **실제로 잰 값만.** 못 잰 값은 0으로 채우지 않고 빼 둔다 |
| `metricsKind` | 숫자의 성격 | `real` = 시연 가상 고객사 계정에서 실제로 잰 값, `team` = 팀원 계정 반응("팀 내 시연 반응") |
| `measuredAt` | 잰 날짜 | `YYYY-MM-DD`. 미래 날짜나 게시일보다 앞선 날짜는 안 된다 |
| `source` | 출처 | 예: `Instagram 인사이트(7일 기준)`. 같은 기간(예: 게시 후 7일)으로 잰다 |

`metrics`가 있으면 `metricsKind`·`measuredAt`·`source`가 **모두** 있어야 한다(없으면 검사에서 오류). 화면은 날짜와 출처가 같이 있을 때만 숫자를 보여 준다.

## 하이라이트 하나(`highlights[]`)

| 칸 | 뜻 |
| --- | --- |
| `id` | 이름(영문 소문자·숫자·`-`·`_`) |
| `title` | 하이라이트 이름 |
| `cover` | 동그란 표지 이미지 |
| `slides[]` | 안의 스토리. `{ "type": "image"\|"video", "src": "assets/…", "date": "YYYY-MM-DD"(선택) }` |

하이라이트 줄은 **계정에 하이라이트가 있다고 확인된 뒤에** 화면에 넣는다. 없으면 `highlights`는 빈 목록으로 둔다.

## 지킬 것

- 사진·영상·캡션에 **시연용 가상 고객사**라는 성격이 드러나야 한다(`badges`의 "시연 가상 고객사"를 지우지 않는다).
- **사람 얼굴이 나오는 이미지는 쓰지 않는다**(초상권). 검사 도구는 얼굴을 알아보지 못하니 눈으로 확인한다.
- 이 레포는 공개다. 올린 파일은 누구나 볼 수 있다. 개인정보·내부 주소·토큰이 들어간 화면은 가려서 넣는다.
- 팀원 계정의 좋아요·댓글은 "실제 시장 반응"으로 쓰지 않는다. 쓰려면 `metricsKind: "team"`으로 구분한다.
- 캣북스 팔로워가 적어 숫자가 작게 나온다. 크게 보이게 꾸미지 않고 날짜·기간과 함께 그대로 보여 준다.
- 파일 이름은 영문 소문자·숫자·`-`·`_`만 쓴다.

## 새 게시물을 올린 날 하는 일

1. 인스타에 게시한다(사람 승인 뒤).
2. 사진·영상을 `assets/<고객사>/`에 넣는다(2MB·15MB 이하).
3. `instagram.json`의 해당 고객사 `posts`에 항목을 추가한다(`example.json` 참고).
4. `node docs/promo/instagram/check.mjs`로 검사해 오류가 없는지 본다.
5. `node docs/promo/build.mjs`로 페이지를 다시 만들어 화면에서 확인하고 PR을 올린다.

반응 숫자는 게시 뒤 같은 기간이 지나면 인사이트에서 읽어 `metrics`·`metricsKind`·`measuredAt`·`source`를 채워 넣는다.

## 검사

```bash
node docs/promo/instagram/check.mjs                              # instagram.json 검사(파일도 확인)
node docs/promo/instagram/check.mjs example.json --no-files      # 예시 형식만 검사
```

오류가 있으면 종료 코드 1이다. 검사하는 것: 필수 칸, 날짜 형식(미래 날짜 금지), 원본 주소 형식, 파일 이름·위치·크기, `aiGenerated`·`axcellent`가 true/false인지, `axcellent`가 true일 때 캡션의 `시연용`, 반응 숫자와 잰 날짜·출처·성격이 함께 있는지.

## 화면 쪽 연결 (병환)

`content.mjs`에서 한 줄로 가져다 쓸 수 있다.

```js
import instagram from "./instagram/load.mjs";

// cases: { ..., items: instagram.casesItems, ... }
```

- `instagram.casesItems`는 (이름은 예전 그대로지만) 콘텐츠 칸 `posts.instagram`에 넣는 모양(`name`, `badges`, `summary`, `posts`)이다. `posts` 항목은 `casesPost`가 쓰는 칸(`img`, `alt`, `caption`, `date`, `metrics`, `measuredAt`, `source`)을 그대로 갖고 있고, `date`·`measuredAt`은 "11월 13일" 같은 글자로 바꿔 준다.
- 추가로 `permalink`(→ "인스타에서 보기" 링크), `aiGenerated`(→ "AI가 만든" 표기), `axcellent`, `type`, `media`, `metricsKind`가 들어 있다. `metricsKind`가 `team`이면 `source` 앞에 "팀 내 시연 반응 · "이 자동으로 붙는다.
- `instagram.customers[].highlights`에 하이라이트(`title`, `cover`, `slides`)가 들어 있다. 하이라이트 줄 화면은 계정에 하이라이트가 있다고 확인된 뒤에 붙인다.
- 사진·영상 경로는 `index.html` 기준(`instagram/assets/…`)으로 바꿔 준다. 빌드 결과물에 `instagram/` 폴더가 함께 복사되어야 한다.
- 자료에 오류가 있으면 `load.mjs`가 만드는 단계에서 오류를 내고 멈춘다(잘못된 숫자·표기가 화면에 나가지 않게).
