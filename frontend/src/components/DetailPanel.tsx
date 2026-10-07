import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { fetchPodLogs, requestDiagnosis } from "../api/client";
import type { EventSummary, PodSummary, Target } from "../api/types";
import { DiagnosisPanel } from "./DiagnosisPanel";
import { EventList } from "./tables";

const LOG_REFRESH_MS = 5000;

function LogViewer({ pod }: { pod: PodSummary }) {
  const [container, setContainer] = useState(pod.containers[0] ?? "");
  const [previous, setPrevious] = useState(false);
  const logs = useQuery({
    queryKey: ["logs", pod.namespace, pod.name, container, previous],
    queryFn: () => fetchPodLogs(pod.namespace, pod.name, container, previous),
    enabled: container !== "",
    refetchInterval: previous ? false : LOG_REFRESH_MS,
  });

  return (
    <section>
      <div className="flex items-center gap-3 text-sm">
        <h3 className="font-medium">Logs</h3>
        <select
          aria-label="Container"
          className="rounded border border-slate-300 px-1 py-0.5"
          value={container}
          onChange={(event) => setContainer(event.target.value)}
        >
          {pod.containers.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={previous}
            onChange={(event) => setPrevious(event.target.checked)}
          />
          previous instance
        </label>
      </div>
      <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-900 p-2 text-xs text-slate-100">
        {logs.isPending && "Loading…"}
        {logs.isError && `Could not load logs: ${logs.error.message}`}
        {logs.data && (logs.data.available ? logs.data.text : "No logs for this container.")}
      </pre>
    </section>
  );
}

interface DetailPanelProps {
  target: Target;
  pod: PodSummary | undefined;
  events: EventSummary[];
  onClose: () => void;
}

export function DetailPanel({ target, pod, events, onClose }: DetailPanelProps) {
  const diagnosis = useMutation({ mutationFn: requestDiagnosis });

  return (
    <aside className="space-y-4 border-l border-slate-200 bg-slate-50 p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">
            {target.kind} · {target.namespace}
          </p>
          <h2 className="break-all text-lg font-semibold">{target.name}</h2>
        </div>
        <button className="text-sm text-slate-500 hover:text-slate-900" onClick={onClose}>
          Close
        </button>
      </header>

      <section>
        <button
          className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-800 disabled:opacity-60"
          disabled={diagnosis.isPending}
          onClick={() => diagnosis.mutate(target)}
        >
          {diagnosis.isPending ? "Collecting evidence and asking the model…" : "Explain this failure"}
        </button>
        {diagnosis.isError && (
          <p role="alert" className="mt-2 text-sm text-red-700">
            Diagnosis failed: {diagnosis.error.message}
          </p>
        )}
        {diagnosis.data && (
          <div className="mt-3">
            <DiagnosisPanel result={diagnosis.data} />
          </div>
        )}
      </section>

      {pod && <LogViewer pod={pod} />}

      <section>
        <h3 className="text-sm font-medium">Events for this object</h3>
        <div className="mt-1 rounded border border-slate-200 bg-white">
          <EventList items={events} />
        </div>
      </section>
    </aside>
  );
}
