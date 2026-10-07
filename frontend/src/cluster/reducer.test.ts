import { describe, expect, it } from "vitest";

import { clusterStreamUrl } from "../api/client";
import type { PodSummary, StreamMessage } from "../api/types";
import { applyMessage, emptyCluster, failingFirst, namespacesOf } from "./reducer";
import { retryDelay } from "./useClusterStream";

function pod(name: string, overrides: Partial<PodSummary> = {}): PodSummary {
  return {
    id: `shop/${name}`,
    namespace: "shop",
    name,
    status: "Running",
    failing: false,
    ready: 1,
    total: 1,
    restarts: 0,
    node: "worker-1",
    owner: null,
    containers: ["app"],
    createdAt: null,
    ...overrides,
  };
}

function snapshot(...pods: PodSummary[]): StreamMessage {
  return { type: "snapshot", collections: { pods, workloads: [], events: [] } };
}

describe("applyMessage", () => {
  it("loads a snapshot", () => {
    const state = applyMessage(emptyCluster, snapshot(pod("a"), pod("b")));
    expect(Object.keys(state.pods)).toEqual(["shop/a", "shop/b"]);
  });

  it("drops objects missing from a later snapshot, as after a reconnect", () => {
    const first = applyMessage(emptyCluster, snapshot(pod("a"), pod("b")));
    const second = applyMessage(first, snapshot(pod("b")));
    expect(Object.keys(second.pods)).toEqual(["shop/b"]);
  });

  it("upserts by id without touching other collections", () => {
    const start = applyMessage(emptyCluster, snapshot(pod("a")));
    const failing = pod("a", { status: "CrashLoopBackOff", failing: true });
    const next = applyMessage(start, { type: "upsert", collection: "pods", item: failing });

    expect(next.pods["shop/a"].status).toBe("CrashLoopBackOff");
    expect(next.workloads).toBe(start.workloads);
    expect(start.pods["shop/a"].status).toBe("Running");
  });

  it("deletes by id and ignores unknown ids", () => {
    const start = applyMessage(emptyCluster, snapshot(pod("a")));
    const afterDelete = applyMessage(start, { type: "delete", collection: "pods", id: "shop/a" });
    const afterUnknown = applyMessage(afterDelete, {
      type: "delete",
      collection: "pods",
      id: "shop/zzz",
    });

    expect(afterDelete.pods).toEqual({});
    expect(afterUnknown.pods).toEqual({});
  });
});

describe("view helpers", () => {
  it("lists namespaces once, sorted", () => {
    const state = applyMessage(
      emptyCluster,
      snapshot(pod("a"), pod("b", { id: "infra/b", namespace: "infra" })),
    );
    expect(namespacesOf(state)).toEqual(["infra", "shop"]);
  });

  it("puts failing items first and keeps the rest in id order", () => {
    const sorted = failingFirst([pod("a"), pod("c", { failing: true }), pod("b")]);
    expect(sorted.map((p) => p.name)).toEqual(["c", "a", "b"]);
  });
});

describe("connection helpers", () => {
  it("uses wss on https pages", () => {
    expect(clusterStreamUrl({ protocol: "https:", host: "kube.example.com" })).toBe(
      "wss://kube.example.com/ws/cluster",
    );
    expect(clusterStreamUrl({ protocol: "http:", host: "localhost:5175" })).toBe(
      "ws://localhost:5175/ws/cluster",
    );
  });

  it("backs off exponentially up to a cap", () => {
    expect([0, 1, 2, 3, 10].map(retryDelay)).toEqual([1000, 2000, 4000, 8000, 15000]);
  });
});
