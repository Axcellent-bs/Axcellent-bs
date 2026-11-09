# types (@ax/types)

**자동 생성 전용 — 직접 수정 금지.**

`src/`의 파일은 `scripts/gen-types.sh`가 백엔드(`ax-company-hq/ax-backend`)의 `contracts/openapi/*.json`에서 생성한다.
타입이 틀리면 여기를 고치지 말고 백엔드 API를 고친 뒤 다시 생성한다.

```bash
npm run gen:types -- <백엔드 ref>      # 예: v0.20261021.0900 또는 커밋 SHA
```

| 파일 | 원본 |
| --- | --- |
| `src/shop-api.ts` | `contracts/openapi/shop-api.json` |
| `src/dashboard-api.ts` | `contracts/openapi/dashboard-api.json` |

앱에서는 `import type { paths } from '@ax/types/shop-api'`처럼 쓴다.
