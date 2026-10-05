import type { DeparturesResponse, ModelInfo, Problem, Station } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | null;

  constructor(status: number, problem: Problem | null) {
    super(problem?.title ?? `HTTP ${status}`);
    this.status = status;
    this.problem = problem;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    let problem: Problem | null;
    try {
      problem = (await response.json()) as Problem;
    } catch {
      problem = null;
    }
    throw new ApiError(response.status, problem);
  }
  return (await response.json()) as T;
}

export const api = {
  stations: (query: string, signal?: AbortSignal) =>
    getJson<Station[]>(`/api/v1/stations?q=${encodeURIComponent(query)}`, signal),
  departures: (eva: string, hours: number, signal?: AbortSignal) =>
    getJson<DeparturesResponse>(
      `/api/v1/stations/${encodeURIComponent(eva)}/departures?hours=${hours}`,
      signal,
    ),
  model: (signal?: AbortSignal) => getJson<ModelInfo>("/api/v1/model", signal),
};
