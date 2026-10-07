import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DiagnosisResponse, Hypothesis } from "../api/types";
import { DiagnosisPanel } from "./DiagnosisPanel";

afterEach(cleanup);

function hypothesis(overrides: Partial<Hypothesis>): Hypothesis {
  return {
    rank: 1,
    category: "oom_killed",
    title: "Container exceeds its memory limit",
    confidence: 0.82,
    explanation: "The worker allocates past 64Mi and is killed.",
    evidence: ["lastState.terminated.reason: OOMKilled", "limits.memory: 64Mi"],
    next_command: "kubectl top pod -n shop",
    command_is_read_only: true,
    ...overrides,
  };
}

function result(...hypotheses: Hypothesis[]): DiagnosisResponse {
  return {
    id: 1,
    created_at: "2026-09-28T09:30:00Z",
    target: { kind: "Deployment", namespace: "shop", name: "report-worker" },
    model: "claude-opus-5-5",
    diagnosis: { summary: "The worker runs out of memory.", hypotheses },
  };
}

describe("DiagnosisPanel", () => {
  it("shows hypotheses in rank order with evidence and the next command", () => {
    render(
      <DiagnosisPanel
        result={result(
          hypothesis({}),
          hypothesis({ rank: 2, title: "Liveness probe too strict", confidence: 0.1 }),
        )}
      />,
    );

    const cards = screen.getAllByRole("listitem").filter((item) => item.querySelector("h4"));
    expect(cards).toHaveLength(2);
    const first = within(cards[0]);
    expect(first.getByText("1. Container exceeds its memory limit")).toBeDefined();
    expect(first.getByText(/82%/)).toBeDefined();
    expect(first.getByText("limits.memory: 64Mi")).toBeDefined();
    expect(first.getByText("kubectl top pod -n shop")).toBeDefined();
    expect(within(cards[1]).getByText(/10%/)).toBeDefined();
    expect(screen.getByText("The worker runs out of memory.")).toBeDefined();
  });

  it("warns when the suggested command is not read-only", () => {
    render(
      <DiagnosisPanel
        result={result(
          hypothesis({
            next_command: "kubectl rollout restart deployment/report-worker",
            command_is_read_only: false,
          }),
        )}
      />,
    );
    expect(screen.getByText(/Review it before running/)).toBeDefined();
  });

  it("does not warn for read-only commands", () => {
    render(<DiagnosisPanel result={result(hypothesis({}))} />);
    expect(screen.queryByText(/Review it before running/)).toBeNull();
  });
});
