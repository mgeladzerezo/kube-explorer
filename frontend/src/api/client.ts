import type { DiagnosisResponse, PodLogs, Target } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function fetchPodLogs(
  namespace: string,
  pod: string,
  container: string,
  previous: boolean,
): Promise<PodLogs> {
  const query = new URLSearchParams({ container, previous: String(previous), tail: "200" });
  return request(`/api/pods/${namespace}/${pod}/logs?${query}`);
}

export function requestDiagnosis(target: Target): Promise<DiagnosisResponse> {
  return request("/api/diagnoses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(target),
  });
}

export function clusterStreamUrl(location: Pick<Location, "protocol" | "host">): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/cluster`;
}
