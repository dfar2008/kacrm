/**
 * Anti-corruption rule for the Decisions surface (spec §四).
 *
 * The decisions page has explicit allow/deny lists for component imports
 * — dashboard-style components (StatCard / DataTable / Panel / etc.)
 * MUST NOT appear in any file under `pages/Decisions*` or
 * `components/decisions/`. This rule enforces that at lint time so a
 * future refactor doesn't silently turn /app/decisions into BI v2.
 *
 * Spec ref: docs/superpowers/specs/2026-05-10-decisions-page-design.md §四.
 */

const FORBIDDEN_PATTERNS = [
  /\/pages\/Analytics/,
  /\/StatCard/,
  /\/DataTable/,
  /\/dashboard\//,
];

// Same forbid list as PATTERNS but matched against named imports.
// ``DecisionCard`` is intentionally excepted — it's the one allowed
// "Card"-named component in this surface.
const FORBIDDEN_NAME_REGEX = /^(StatCard|DataTable|Dashboard|Panel)$/;

const rule = {
  meta: {
    type: "problem",
    docs: {
      description: "Forbid dashboard/stat imports in Decisions* files",
    },
    schema: [],
    messages: {
      forbiddenSource:
        'Decisions files must not import "{{src}}" — see spec §四 anti-corruption.',
      forbiddenName:
        'Decisions files cannot import "{{name}}" (matches forbidden dashboard pattern).',
    },
  },
  create(context) {
    const filename = (context.filename || context.getFilename()).replace(
      /\\/g, "/",
    );
    const isDecisionsFile =
      filename.includes("/pages/Decisions") ||
      filename.includes("/components/decisions/");
    if (!isDecisionsFile) return {};

    return {
      ImportDeclaration(node) {
        const src = node.source.value;
        for (const pat of FORBIDDEN_PATTERNS) {
          if (pat.test(src)) {
            context.report({
              node,
              messageId: "forbiddenSource",
              data: { src },
            });
          }
        }
        for (const spec of node.specifiers) {
          const name =
            (spec.imported && spec.imported.name) ||
            (spec.local && spec.local.name);
          if (
            name &&
            FORBIDDEN_NAME_REGEX.test(name) &&
            name !== "DecisionCard"
          ) {
            context.report({
              node,
              messageId: "forbiddenName",
              data: { name },
            });
          }
        }
      },
    };
  },
};

export default rule;
