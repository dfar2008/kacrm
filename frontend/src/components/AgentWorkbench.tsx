import { CopilotPanel } from "@/components/CopilotPanel";

/**
 * Agent 工作台 — Charter v2 Phase 3 simplification (2026-05-07).
 *
 * The original implementation was a 4-card workbench (Research / Drafts /
 * Analyst / Coach) backed by ``/api/agents/by_record``. Charter v2
 * Phase 3 (commit 7590d6c) removed:
 *   - the per-record agent subsystem (no more "this account has its own
 *     research agent state"),
 *   - the approval-gated draft queue ("AI 驾驶 + 人类监督" no longer
 *     means a manual approval step for every action — actions
 *     auto-execute, gateway just records + revertable audit),
 *   - the RL feedback loop (no training, no "采纳 / 拒绝 / 修订"
 *     telemetry).
 *
 * What remains is a single conversational surface: the user asks the
 * agent something about this record, the agent uses CRM tools to act.
 * That is :class:`CopilotPanel`. AgentWorkbench is now just a thin
 * compatibility shim so existing callers (Crm.tsx, the slide-in
 * overlay) don't have to be updated.
 *
 * Coach hints, when they exist, surface in the InteractionTimeline
 * (kind="coach_hint") not as a separate card pile.
 */

interface Props {
  module?: string;
  recordId?: number;
  prefill?: { text: string; nonce: number } | null;
  onRecordUpdated?: () => void;
}

export function AgentWorkbench(props: Props) {
  return <CopilotPanel {...props} />;
}
