type JsonSchema = Record<string, unknown>;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length
      && left.every((item, index) => jsonEqual(item, right[index]));
  }
  if (isObject(left) && isObject(right)) {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index]
        && jsonEqual(left[key], right[key]));
  }
  return false;
}

function hasType(value: unknown, expected: unknown): boolean {
  switch (expected) {
    case "object": return isObject(value);
    case "array": return Array.isArray(value);
    case "string": return typeof value === "string";
    case "number": return typeof value === "number" && Number.isFinite(value);
    case "integer": return typeof value === "number" && Number.isSafeInteger(value);
    case "boolean": return typeof value === "boolean";
    case "null": return value === null;
    default: return false;
  }
}

/** Validate the reference-free schema subset accepted by PilotInferenceContract. */
export function matchesBoundedSchema(value: unknown, schema: JsonSchema, depth = 0): boolean {
  if (depth > 8 || !hasType(value, schema.type)) return false;
  if (Array.isArray(schema.enum) && !schema.enum.some((item) => jsonEqual(item, value))) return false;
  if (Object.hasOwn(schema, "const") && !jsonEqual(schema.const, value)) return false;

  if (typeof value === "string") {
    const codePoints = Array.from(value).length;
    if (typeof schema.minLength === "number" && codePoints < schema.minLength) return false;
    if (typeof schema.maxLength === "number" && codePoints > schema.maxLength) return false;
  }
  if (typeof value === "number") {
    if (typeof schema.minimum === "number" && value < schema.minimum) return false;
    if (typeof schema.maximum === "number" && value > schema.maximum) return false;
    if (typeof schema.exclusiveMinimum === "number" && value <= schema.exclusiveMinimum) return false;
    if (typeof schema.exclusiveMaximum === "number" && value >= schema.exclusiveMaximum) return false;
  }
  if (Array.isArray(value)) {
    if (typeof schema.minItems === "number" && value.length < schema.minItems) return false;
    if (typeof schema.maxItems === "number" && value.length > schema.maxItems) return false;
    if (!isObject(schema.items)) return false;
    return value.every((item) => matchesBoundedSchema(item, schema.items as JsonSchema, depth + 1));
  }
  if (isObject(value)) {
    const properties = isObject(schema.properties) ? schema.properties : {};
    const required = Array.isArray(schema.required) ? schema.required : [];
    if (!required.every((key) => typeof key === "string" && Object.hasOwn(value, key))) return false;
    const keys = Object.keys(value);
    if (typeof schema.minProperties === "number" && keys.length < schema.minProperties) return false;
    if (typeof schema.maxProperties === "number" && keys.length > schema.maxProperties) return false;
    if (schema.additionalProperties !== false) return false;
    return keys.every((key) => {
      const child = properties[key];
      return isObject(child) && matchesBoundedSchema(value[key], child, depth + 1);
    });
  }
  return true;
}

export function utf8Size(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}
