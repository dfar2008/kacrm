import type { CrmFieldMeta } from "./api";

const numericTypes = new Set([
  "number", "int", "integer", "decimal", "float", "currency", "money", "amount", "percent",
]);

export function createValueErrors(fields: CrmFieldMeta[], values: Record<string, unknown>): Record<string, "number"> {
  const errors: Record<string, "number"> = {};
  for (const field of fields) {
    const value = values[field.field_name];
    if (!numericTypes.has(field.field_type.toLowerCase()) || value == null || String(value).trim() === "") continue;
    if (!Number.isFinite(Number(value))) errors[field.field_name] = "number";
  }
  return errors;
}

/** Form controls hold strings; the create API needs typed JSON values. */
export function createRecordValues(fields: CrmFieldMeta[], values: Record<string, unknown>): Record<string, unknown> {
  const types = new Map(fields.map(field => [field.field_name, field.field_type.toLowerCase()]));
  const payload: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(values)) {
    if (value === "" || value === null || value === undefined) continue;
    if (numericTypes.has(types.get(name) || "") && typeof value === "string" && value.trim()) {
      const number = Number(value);
      // Legacy `number` fields can be backed by integer columns. Keep
      // fractional strings exact rather than letting PostgreSQL round them.
      const legacyFraction = types.get(name) === "number" && !Number.isInteger(number);
      payload[name] = Number.isFinite(number) && !legacyFraction ? number : value;
    } else {
      payload[name] = value;
    }
  }
  return payload;
}
