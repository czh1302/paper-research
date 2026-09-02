import { describe, expect, it } from "vitest";
import { deriveWorkflowState, mergeWorkflowState } from "./job-workflow";
import type { JobEvent, JobRecord } from "./types";

const job: Pick<JobRecord, "status" | "stage" | "progress"> = {
  status: "searching",
  stage: "v4_full_text",
  progress: 52,
};

function event(id: number, kind: string, data: Record<string, unknown>): JobEvent {
  return { id, kind, data, message: "", created_at: `2026-09-03T00:0${id}:00Z` };
}

describe("job workflow derivation", () => {
  it("does not regress after Idea work has started", () => {
    const state = deriveWorkflowState(job, [
      event(1, "idea_attempt", { attempt: 1, max_attempts: 3 }),
      event(2, "stage", { workflow_stage: "v4_full_text", substage: "full_text_ranking", progress: 52 }),
    ]);
    expect(state.step).toBe(5);
    expect(state.progress).toBe(74);
    expect(state.latestEvent?.id).toBe(1);
  });

  it("keeps a locally observed milestone across an out-of-order update", () => {
    const idea = deriveWorkflowState({ status: "analyzing", stage: "v4_ideas", progress: 84 });
    const stale = deriveWorkflowState(job);
    expect(mergeWorkflowState(idea, stale)).toEqual(idea);
  });

  it("keeps PilotSpecification within the sixth main step", () => {
    const state = deriveWorkflowState(
      { status: "analyzing", stage: "v4_pilot_specification", progress: 90 },
      [event(1, "stage", { workflow_stage: "v4_pilot_specification", substage: "pilot_specification", progress: 90 })],
    );
    expect(state.step).toBe(5);
    expect(state.substage).toBe("pilot_specification");
  });
});
