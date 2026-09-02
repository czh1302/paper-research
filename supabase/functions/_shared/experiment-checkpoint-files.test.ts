import { describe, expect, it } from "vitest";
import {
  checkpointSourceFiles,
  legacyCheckpointFileEntries,
} from "./experiment-checkpoint-files.ts";

describe("checkpointSourceFiles", () => {
  it("returns only completed manifest files in deterministic path order", () => {
    const checkpoint = {
      manifest: {
        files: [
          { path: "README.md", batch: 1 },
          { path: "src/main.py", batch: 2 },
          { path: "tests/test_main.py", batch: 3 },
        ],
      },
      file_batches: {
        "2": { files: [
          { path: "src/main.py", content: "print('ready')\n" },
          { path: "private.txt", content: "not declared" },
        ] },
        "1": { files: [
          { path: "README.md", content: "# Pilot\n" },
          { path: "src/main.py", content: "malformed duplicate" },
        ] },
      },
      provider_token: "must never be returned",
    };

    expect(checkpointSourceFiles(checkpoint)).toEqual([
      { path: "README.md", content: "# Pilot\n", byteSize: 8 },
      { path: "src/main.py", content: "print('ready')\n", byteSize: 15 },
    ]);
  });

  it("rejects protected, undeclared and oversized checkpoint values", () => {
    const oversized = "x".repeat(1_000_001);
    const checkpoint = {
      manifest: { files: [
        { path: ".env", batch: 1 },
        { path: "src/ok.py", batch: 1 },
        { path: "src/large.py", batch: 1 },
      ] },
      file_batches: { "1": { files: [
        { path: ".env", content: "SECRET=value" },
        { path: "src/not-planned.py", content: "hidden" },
        { path: "src/ok.py", content: "ok = True\n" },
        { path: "src/large.py", content: oversized },
      ] } },
    };

    expect(checkpointSourceFiles(checkpoint)).toEqual([
      { path: "src/ok.py", content: "ok = True\n", byteSize: 10 },
    ]);
  });

  it("does not expose a batch without its frozen manifest", () => {
    expect(checkpointSourceFiles({
      file_batches: { "1": { files: [{ path: "src/main.py", content: "secret" }] } },
    })).toEqual([]);
  });

  it("is incremental and idempotent across resumed batch checkpoints", () => {
    const checkpoint: Record<string, unknown> = {
      manifest: { files: [
        { path: "README.md", batch: 1 },
        { path: "src/main.py", batch: 2 },
      ] },
      file_batches: {
        "1": { files: [{ path: "README.md", content: "# Ready\n" }] },
      },
    };
    const first = checkpointSourceFiles(checkpoint);
    expect(checkpointSourceFiles(checkpoint)).toEqual(first);
    expect(first.map((file) => file.path)).toEqual(["README.md"]);

    checkpoint.file_batches = {
      ...(checkpoint.file_batches as Record<string, unknown>),
      "2": { files: [{ path: "src/main.py", content: "print('ready')\n" }] },
    };
    expect(checkpointSourceFiles(checkpoint).map((file) => file.path)).toEqual([
      "README.md",
      "src/main.py",
    ]);
  });
});

describe("legacyCheckpointFileEntries", () => {
  it("forwards only validated workspace metadata", () => {
    const result = legacyCheckpointFileEntries({
      repository_tree: [
        { path: "src", type: "directory", content: "not forwarded" },
        { path: "src/main.py", type: "file", size: 12, sha256: "a".repeat(64), secret: "hidden" },
        { path: ".research-atlas/pilot-spec.json", type: "file", size: 12 },
        { path: "../escape", type: "file", size: 12 },
      ],
    });

    expect(result).toEqual([
      { path: "src", type: "directory" },
      { path: "src/main.py", type: "file", size: 12, sha256: "a".repeat(64) },
    ]);
    expect(JSON.stringify(result)).not.toContain("hidden");
    expect(JSON.stringify(result)).not.toContain("not forwarded");
  });
});
