import type { Validation } from "./types";

export class ApiError extends Error {
  /** `code`: 서버 오류 코드(`{error: {code}}`). 없으면 비어 있다 */
  constructor(message: string, public status: number, public validation?: Validation, public code?: string | null) {
    super(message);
  }
}
