import type { Validation } from "./types";

export class ApiError extends Error {
  constructor(message: string, public status: number, public validation?: Validation) {
    super(message);
  }
}
