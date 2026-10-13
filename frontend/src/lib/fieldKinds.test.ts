import { isPicklistLike, isMultiselectLike, isSelectLike } from "./fieldKinds";

// Regression: alembic 0107 renamed every select/radio → picklist in
// module_fields, and CreateRecordModal's hand-rolled `ft === "select"`
// check silently degraded ALL dropdowns (状态/行业/…) into bare text
// inputs — on dev AND prod. These lock the canonical types in.

test("canonical picklist renders as dropdown", () => {
  expect(isPicklistLike("picklist")).toBe(true);
});

test("canonical multiselect renders as checkbox group", () => {
  expect(isMultiselectLike("multiselect")).toBe(true);
});

test("legacy single-select aliases still match", () => {
  for (const ft of ["select", "radio", "enum", "option", "Picklist", " select "]) {
    expect(isPicklistLike(ft)).toBe(true);
  }
});

test("legacy multi-select alias still matches", () => {
  expect(isMultiselectLike("multipicklist")).toBe(true);
  expect(isMultiselectLike("multiSelect")).toBe(true);
});

test("non-select types stay free-form", () => {
  for (const ft of ["text", "number", "user", "relation", "date", "", null, undefined]) {
    expect(isSelectLike(ft)).toBe(false);
  }
});

test("picklist and multiselect are disjoint", () => {
  expect(isMultiselectLike("picklist")).toBe(false);
  expect(isPicklistLike("multiselect")).toBe(false);
});
