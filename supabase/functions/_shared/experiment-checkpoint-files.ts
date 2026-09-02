export type CheckpointSourceFile = {
  path: string;
  content: string;
  byteSize: number;
};

export type CheckpointFileEntry = {
  path: string;
  type: "file" | "directory";
  size?: number;
  sha256?: string;
  updatedAt?: string;
};

const MAX_GENERATED_FILES = 48;
const MAX_GENERATED_FILE_BYTES = 1_000_000;
const MAX_REPOSITORY_PATH_BYTES = 240;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function safeCheckpointPath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.replaceAll("\\", "/").trim().replace(/^\.\//, "");
  const encodedLength = new TextEncoder().encode(normalized).byteLength;
  if (
    encodedLength < 1
    || encodedLength > MAX_REPOSITORY_PATH_BYTES
    || normalized.startsWith("/")
    || [...normalized].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127;
    })
  ) return null;
  const segments = normalized.split("/");
  if (segments.some((part) => !part || part === "." || part === "..")) return null;
  if (segments.some((part) =>
    part === ".git"
    || part === ".research-atlas"
    || part === ".env"
    || part.startsWith(".env.")
  )) return null;
  return normalized;
}

function orderedBatchValues(value: unknown): Array<{ batchNumber: number | null; value: Record<string, unknown> }> {
  const batches = record(value);
  if (!batches) return [];
  return Object.entries(batches)
    .sort(([left], [right]) => {
      const leftNumber = Number(left);
      const rightNumber = Number(right);
      if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
        return leftNumber - rightNumber;
      }
      return left.localeCompare(right);
    })
    .flatMap(([key, batch]) => {
      const parsed = record(batch);
      const numericKey = Number(key);
      const batchNumber = Number.isSafeInteger(numericKey) ? numericKey : null;
      return parsed ? [{ batchNumber, value: parsed }] : [];
    });
}

/**
 * Extract only completed, manifest-declared generated files from a Worker
 * checkpoint. Callers must never serialize the surrounding checkpoint: it
 * contains private execution state that is unrelated to the code workspace.
 */
export function checkpointSourceFiles(checkpointValue: unknown): CheckpointSourceFile[] {
  const checkpoint = record(checkpointValue);
  const manifest = record(checkpoint?.manifest);
  const plans = Array.isArray(manifest?.files) ? manifest.files : [];
  const allowed = new Map<string, number>();
  for (const planValue of plans.slice(0, MAX_GENERATED_FILES)) {
    const plan = record(planValue);
    const path = safeCheckpointPath(plan?.path);
    if (path && typeof plan?.batch === "number" && Number.isSafeInteger(plan.batch)) {
      allowed.set(path, plan.batch);
    }
  }
  if (!allowed.size) return [];

  const completed = new Map<string, CheckpointSourceFile>();
  for (const batch of orderedBatchValues(checkpoint?.file_batches)) {
    if (!Array.isArray(batch.value.files)) continue;
    for (const fileValue of batch.value.files) {
      const file = record(fileValue);
      const path = safeCheckpointPath(file?.path);
      if (
        !path
        || allowed.get(path) !== batch.batchNumber
        || typeof file?.content !== "string"
      ) continue;
      const byteSize = new TextEncoder().encode(file.content).byteLength;
      if (byteSize > MAX_GENERATED_FILE_BYTES) continue;
      // A valid Worker checkpoint has each manifest path in exactly one batch.
      // Keeping the first completed copy makes malformed duplicate batches
      // deterministic and prevents a later injected value from shadowing it.
      if (!completed.has(path)) {
        completed.set(path, { path, content: file.content, byteSize });
      }
      if (completed.size >= MAX_GENERATED_FILES) break;
    }
    if (completed.size >= MAX_GENERATED_FILES) break;
  }
  return [...completed.values()].sort((left, right) => left.path.localeCompare(right.path));
}

/** Sanitize legacy checkpoint tree metadata without forwarding extra fields. */
export function legacyCheckpointFileEntries(checkpointValue: unknown): CheckpointFileEntry[] {
  const checkpoint = record(checkpointValue);
  const candidate = checkpoint?.repository_tree ?? checkpoint?.file_tree ?? checkpoint?.files;
  if (!Array.isArray(candidate)) return [];
  const entries = new Map<string, CheckpointFileEntry>();
  for (const value of candidate.slice(0, MAX_GENERATED_FILES * 2)) {
    const item = record(value);
    const path = safeCheckpointPath(item?.path);
    const type = item?.type === "directory" ? "directory" : item?.type === "file" ? "file" : null;
    if (!path || !type || entries.has(path)) continue;
    const entry: CheckpointFileEntry = { path, type };
    if (typeof item?.size === "number" && Number.isSafeInteger(item.size) && item.size >= 0) {
      entry.size = Math.min(item.size, MAX_GENERATED_FILE_BYTES);
    }
    if (typeof item?.sha256 === "string" && /^[0-9a-f]{64}$/i.test(item.sha256)) {
      entry.sha256 = item.sha256.toLowerCase();
    }
    if (typeof item?.updatedAt === "string" && item.updatedAt.length <= 64) {
      entry.updatedAt = item.updatedAt;
    }
    entries.set(path, entry);
  }
  return [...entries.values()].sort((left, right) => left.path.localeCompare(right.path));
}
