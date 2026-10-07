import { useMemo, useState } from "react";

import type { Target } from "./api/types";
import { failingFirst, namespacesOf } from "./cluster/reducer";
import { useClusterStream, type ConnectionStatus } from "./cluster/useClusterStream";
import { DetailPanel } from "./components/DetailPanel";
import { EventList, PodTable, WorkloadTable } from "./components/tables";

type Tab = "workloads" | "pods" | "events";
const TABS: Tab[] = ["workloads", "pods", "events"];

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
};

export default function App() {
  const { cluster, status } = useClusterStream();
  const [tab, setTab] = useState<Tab>("workloads");
  const [namespace, setNamespace] = useState("");
  const [failingOnly, setFailingOnly] = useState(false);
  const [selected, setSelected] = useState<Target | null>(null);

  const view = useMemo(() => {
    const inNamespace = <T extends { namespace: string }>(items: T[]) =>
      namespace ? items.filter((item) => item.namespace === namespace) : items;
    const visible = <T extends { failing: boolean }>(items: T[]) =>
      failingOnly ? items.filter((item) => item.failing) : items;

    const events = inNamespace(Object.values(cluster.events)).sort((a, b) =>
      (b.lastSeen ?? "").localeCompare(a.lastSeen ?? ""),
    );
    return {
      namespaces: namespacesOf(cluster),
      workloads: failingFirst(visible(inNamespace(Object.values(cluster.workloads)))),
      pods: failingFirst(visible(inNamespace(Object.values(cluster.pods)))),
      events: failingOnly ? events.filter((event) => event.type === "Warning") : events,
    };
  }, [cluster, namespace, failingOnly]);

  const failingCount = Object.values(cluster.pods).filter((pod) => pod.failing).length;
  const selectedPod =
    selected?.kind === "Pod" ? cluster.pods[`${selected.namespace}/${selected.name}`] : undefined;
  const selectedEvents = selected
    ? Object.values(cluster.events).filter(
        (event) =>
          event.namespace === selected.namespace &&
          event.object === `${selected.kind}/${selected.name}`,
      )
    : [];

  return (
    <div className="min-h-screen bg-white text-slate-900">
      <header className="flex flex-wrap items-center gap-4 border-b border-slate-200 px-4 py-3">
        <h1 className="text-lg font-semibold">Kube Explorer</h1>
        <span
          className={`text-sm ${status === "live" ? "text-emerald-700" : "text-amber-700"}`}
          role="status"
        >
          {STATUS_LABEL[status]}
        </span>
        <span className="text-sm text-slate-600">{failingCount} failing pods</span>
        <label className="ml-auto flex items-center gap-2 text-sm">
          Namespace
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={namespace}
            onChange={(event) => setNamespace(event.target.value)}
          >
            <option value="">All</option>
            {view.namespaces.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={failingOnly}
            onChange={(event) => setFailingOnly(event.target.checked)}
          />
          Failing only
        </label>
      </header>

      <div className={selected ? "grid lg:grid-cols-[minmax(0,1fr)_28rem]" : ""}>
        <main>
          <nav className="flex gap-1 border-b border-slate-200 px-3 pt-2">
            {TABS.map((name) => (
              <button
                key={name}
                className={`rounded-t px-3 py-1.5 text-sm capitalize ${
                  tab === name ? "bg-slate-200 font-medium" : "text-slate-600 hover:bg-slate-100"
                }`}
                onClick={() => setTab(name)}
              >
                {name} ({view[name].length})
              </button>
            ))}
          </nav>
          {tab === "workloads" && (
            <WorkloadTable items={view.workloads} selected={selected} onSelect={setSelected} />
          )}
          {tab === "pods" && (
            <PodTable items={view.pods} selected={selected} onSelect={setSelected} />
          )}
          {tab === "events" && <EventList items={view.events} />}
        </main>

        {selected && (
          <DetailPanel
            // Remount per target so a diagnosis never shows under the wrong object.
            key={`${selected.kind}/${selected.namespace}/${selected.name}`}
            target={selected}
            pod={selectedPod}
            events={selectedEvents}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}
