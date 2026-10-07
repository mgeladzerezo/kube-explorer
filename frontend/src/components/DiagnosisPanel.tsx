import type { DiagnosisResponse, Hypothesis } from "../api/types";

function percent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

function HypothesisCard({ hypothesis }: { hypothesis: Hypothesis }) {
  return (
    <li className="rounded border border-slate-200 bg-white p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="font-medium">
          {hypothesis.rank}. {hypothesis.title}
        </h4>
        <span className="shrink-0 text-xs text-slate-500">
          {percent(hypothesis.confidence)} · {hypothesis.category}
        </span>
      </div>
      <p className="mt-1 text-sm text-slate-700">{hypothesis.explanation}</p>

      <h5 className="mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">Evidence</h5>
      <ul className="list-disc pl-5 text-sm text-slate-700">
        {hypothesis.evidence.map((item) => (
          <li key={item} className="break-words">
            {item}
          </li>
        ))}
      </ul>

      <h5 className="mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        Next command
      </h5>
      <pre className="overflow-x-auto rounded bg-slate-900 p-2 text-xs text-slate-100">
        {hypothesis.next_command}
      </pre>
      {!hypothesis.command_is_read_only && (
        <p className="mt-1 text-xs text-amber-700">
          This command is not a plain read-only kubectl call. Review it before running.
        </p>
      )}
    </li>
  );
}

export function DiagnosisPanel({ result }: { result: DiagnosisResponse }) {
  return (
    <section aria-label="Diagnosis">
      <p className="text-sm">{result.diagnosis.summary}</p>
      <ol className="mt-2 space-y-2">
        {result.diagnosis.hypotheses.map((hypothesis) => (
          <HypothesisCard key={hypothesis.rank} hypothesis={hypothesis} />
        ))}
      </ol>
      <p className="mt-2 text-xs text-slate-500">
        {result.model} · {new Date(result.created_at).toLocaleString()} · secrets were redacted
        before the evidence left the cluster
      </p>
    </section>
  );
}
