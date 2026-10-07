import type { EventSummary, PodSummary, Target, WorkloadSummary } from "../api/types";

const cell = "px-3 py-1.5 text-left";
const head = `${cell} text-xs font-medium uppercase tracking-wide text-slate-500`;

export function StatusBadge({ failing, label }: { failing: boolean; label: string }) {
  const tone = failing ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800";
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{label}</span>;
}

function rowClass(failing: boolean, selected: boolean): string {
  const base = "cursor-pointer border-t border-slate-200 hover:bg-slate-100";
  const tone = failing ? "bg-red-50" : "";
  return `${base} ${tone} ${selected ? "outline outline-2 -outline-offset-2 outline-sky-500" : ""}`;
}

function isSelected(selected: Target | null, kind: string, item: { namespace: string; name: string }) {
  return (
    selected?.kind === kind && selected.namespace === item.namespace && selected.name === item.name
  );
}

interface TableProps<T> {
  items: T[];
  selected: Target | null;
  onSelect: (target: Target) => void;
}

export function WorkloadTable({ items, selected, onSelect }: TableProps<WorkloadSummary>) {
  if (items.length === 0) return <Empty what="workloads" />;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th className={head}>Namespace</th>
          <th className={head}>Kind</th>
          <th className={head}>Name</th>
          <th className={head}>Ready</th>
        </tr>
      </thead>
      <tbody>
        {items.map((workload) => (
          <tr
            key={workload.id}
            className={rowClass(workload.failing, isSelected(selected, workload.kind, workload))}
            onClick={() =>
              onSelect({ kind: workload.kind, namespace: workload.namespace, name: workload.name })
            }
          >
            <td className={cell}>{workload.namespace}</td>
            <td className={cell}>{workload.kind}</td>
            <td className={`${cell} font-medium`}>{workload.name}</td>
            <td className={cell}>
              <StatusBadge
                failing={workload.failing}
                label={`${workload.ready}/${workload.desired}`}
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PodTable({ items, selected, onSelect }: TableProps<PodSummary>) {
  if (items.length === 0) return <Empty what="pods" />;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th className={head}>Namespace</th>
          <th className={head}>Name</th>
          <th className={head}>Status</th>
          <th className={head}>Ready</th>
          <th className={head}>Restarts</th>
          <th className={head}>Node</th>
        </tr>
      </thead>
      <tbody>
        {items.map((pod) => (
          <tr
            key={pod.id}
            className={rowClass(pod.failing, isSelected(selected, "Pod", pod))}
            onClick={() => onSelect({ kind: "Pod", namespace: pod.namespace, name: pod.name })}
          >
            <td className={cell}>{pod.namespace}</td>
            <td className={`${cell} font-medium`}>{pod.name}</td>
            <td className={cell}>
              <StatusBadge failing={pod.failing} label={pod.status} />
            </td>
            <td className={cell}>
              {pod.ready}/{pod.total}
            </td>
            <td className={cell}>{pod.restarts}</td>
            <td className={cell}>{pod.node ?? "unscheduled"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EventList({ items }: { items: EventSummary[] }) {
  if (items.length === 0) return <Empty what="events" />;
  return (
    <ul className="divide-y divide-slate-200 text-sm">
      {items.map((event) => (
        <li key={event.id} className={`px-3 py-2 ${event.type === "Warning" ? "bg-amber-50" : ""}`}>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{event.reason}</span>
            <span className="text-slate-600">{event.object}</span>
            <span className="text-xs text-slate-500">
              {event.namespace} · x{event.count}
              {event.lastSeen ? ` · ${new Date(event.lastSeen).toLocaleTimeString()}` : ""}
            </span>
          </div>
          <p className="break-words text-slate-700">{event.message}</p>
        </li>
      ))}
    </ul>
  );
}

function Empty({ what }: { what: string }) {
  return <p className="px-3 py-6 text-sm text-slate-500">No {what} to show.</p>;
}
