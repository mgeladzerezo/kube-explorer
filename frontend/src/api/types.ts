// Shapes returned by the backend. Field names follow the JSON the API emits.

export interface PodSummary {
  id: string;
  namespace: string;
  name: string;
  status: string;
  failing: boolean;
  ready: number;
  total: number;
  restarts: number;
  node: string | null;
  owner: string | null;
  containers: string[];
  createdAt: string | null;
}

export type WorkloadKind = "Deployment" | "StatefulSet" | "DaemonSet";
export type TargetKind = WorkloadKind | "Pod";

export interface WorkloadSummary {
  id: string;
  kind: WorkloadKind;
  namespace: string;
  name: string;
  desired: number;
  ready: number;
  failing: boolean;
  createdAt: string | null;
}

export interface EventSummary {
  id: string;
  namespace: string;
  type: string;
  reason: string;
  object: string;
  message: string;
  count: number;
  lastSeen: string | null;
}

export interface Collections {
  pods: PodSummary[];
  workloads: WorkloadSummary[];
  events: EventSummary[];
}

export type CollectionName = keyof Collections;

export type StreamMessage =
  | { type: "snapshot"; collections: Collections }
  | { type: "upsert"; collection: CollectionName; item: Collections[CollectionName][number] }
  | { type: "delete"; collection: CollectionName; id: string };

export interface Target {
  kind: TargetKind;
  namespace: string;
  name: string;
}

export interface Hypothesis {
  rank: number;
  category: string;
  title: string;
  confidence: number;
  explanation: string;
  evidence: string[];
  next_command: string;
  command_is_read_only: boolean;
}

export interface Diagnosis {
  summary: string;
  hypotheses: Hypothesis[];
}

export interface DiagnosisResponse {
  id: number;
  created_at: string;
  target: Target;
  model: string;
  diagnosis: Diagnosis;
}

export interface PodLogs {
  text: string;
  available: boolean;
}
