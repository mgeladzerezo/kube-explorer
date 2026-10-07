import type { CollectionName, Collections, StreamMessage } from "../api/types";

export type ClusterState = {
  [K in CollectionName]: Record<string, Collections[K][number]>;
};

export const emptyCluster: ClusterState = { pods: {}, workloads: {}, events: {} };

function byId<T extends { id: string }>(items: T[]): Record<string, T> {
  return Object.fromEntries(items.map((item) => [item.id, item]));
}

export function applyMessage(state: ClusterState, message: StreamMessage): ClusterState {
  switch (message.type) {
    case "snapshot":
      // A snapshot replaces everything: after a reconnect, objects deleted in the gap must go.
      return {
        pods: byId(message.collections.pods),
        workloads: byId(message.collections.workloads),
        events: byId(message.collections.events),
      };
    case "upsert":
      return {
        ...state,
        [message.collection]: { ...state[message.collection], [message.item.id]: message.item },
      };
    case "delete": {
      const { [message.id]: _removed, ...rest } = state[message.collection];
      return { ...state, [message.collection]: rest };
    }
  }
}

export function namespacesOf(state: ClusterState): string[] {
  const names = new Set<string>();
  for (const pod of Object.values(state.pods)) names.add(pod.namespace);
  for (const workload of Object.values(state.workloads)) names.add(workload.namespace);
  return [...names].sort();
}

/** Failing items first, then by id, so problems are at the top without hiding the rest. */
export function failingFirst<T extends { id: string; failing: boolean }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => Number(b.failing) - Number(a.failing) || a.id.localeCompare(b.id),
  );
}
