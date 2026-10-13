// frontend/scripts/check-no-raw-colors.mjs
// 扫描已迁移文件里是否残留裸色字面量。每迁移一个文件就加进 CHECKED。
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// 已完成 token 迁移、必须保持 0 裸色的文件(随迁移进度逐步追加):
const CHECKED = [
  "src/index.css",
  "src/pages/ChatInbox.tsx",
  "src/pages/AdminUsers.tsx",
  "src/components/RecordDetail.tsx",
  "src/components/UserProfileModal.tsx",
  "src/pages/agent-detail/ModelTab.tsx",
  "src/pages/onboarding/ModelTemplateEditor.tsx",
  "src/pages/agent-detail/SchedulingTab.tsx",
  "src/pages/AdminRoles.tsx",
  "src/pages/agent-detail/SkillsTab.tsx",
  "src/pages/AdminDepartments.tsx",
  "src/pages/schema/FieldProperties.tsx",
  "src/pages/agent-detail/WorkspaceTab.tsx",
  "src/pages/MemoryEntityDetail.tsx",
  "src/components/CopilotListBrief.tsx",
  "src/components/AppShell.tsx",
  "src/pages/onboarding/SkillsTemplateEditor.tsx",
  "src/pages/Memory.tsx",
  "src/pages/Approvals.tsx",
  "src/pages/AiAudit.tsx",
  "src/pages/AdminOnboardingTemplate.tsx",
  "src/components/ApprovalToast.tsx",
  "src/pages/onboarding/CronTemplateEditor.tsx",
  "src/pages/Tasks.tsx",
  "src/components/AllInfoDrawer.tsx",
  "src/pages/AdminSchemaChangelog.tsx",
  "src/components/RecordInsightCard.tsx",
  "src/pages/schema/NewSubdetailModal.tsx",
  "src/pages/schema/NewModuleModal.tsx",
  "src/pages/AdminFieldProposals.tsx",
  "src/components/dashboards/ChatPanel.tsx",
  "src/components/TodayPriority.tsx",
  "src/components/TodayCoachCards.tsx",
  "src/pages/agent-detail/ChannelsTab.tsx",
  "src/pages/MemoryDebug.tsx",
  "src/pages/Crm.tsx",
  "src/components/dashboards/DashboardChart.tsx",
  "src/components/TodayDecisionPrompts.tsx",
  "src/pages/schema/WritebackRulesModal.tsx",
  "src/pages/agent-detail/ChannelSetup/FeishuSetup.tsx",
  "src/pages/agent-detail/ChannelSetup/WeixinSetup.tsx",
  "src/pages/agent-detail/ChannelSetup/WecomSetup.tsx",
  "src/pages/agent-detail/ChannelSetup/DingtalkSetup.tsx",
  "src/components/RecordList.tsx",
  "src/components/ListFieldsCustomizer.tsx",
  "src/pages/schema/FieldCard.tsx",
  "src/pages/agent-detail/AddModelModal.tsx",
  "src/pages/AdminSchemaHub.tsx",
  "src/lib/toast.tsx",
  "src/components/SegmentsConfig.tsx",
  "src/components/MarkdownText.tsx",
  "src/components/InteractionTimeline.tsx",
  "src/components/HeadlineCustomizer.tsx",
  "src/components/CronHealthLine.tsx",
  "src/components/AIFocus.tsx",
  "src/pages/schema/FieldLayout.tsx",
  "src/lib/coachHintVisuals.ts",
  "src/components/WeeklyAiReportCard.tsx",
  "src/components/ViewBuilder.tsx",
  "src/components/TodayPriorityTabs.tsx",
  "src/components/TeamBriefSection.tsx",
  "src/components/TeamAuditDashboard.tsx",
  "src/components/ModelPicker.tsx",
  "src/components/DailyFeed.tsx",
  "src/pages/schema/ComponentPalette.tsx",
  "src/pages/agent-detail/ChannelSetup/FormBotSetup.tsx",
  "src/components/views/CalendarView.tsx",
  "src/components/dashboards/DrillModal.tsx",
  "src/components/CreateRecordModal.tsx",
  "src/pages/schema/SchemaAssistant.tsx",
  "src/pages/AdminUsage.tsx",
  "src/components/ShareRecordModal.tsx",
  "src/components/LineItemsEditor.tsx",
];

const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;
// 豁免 ①:CSS 自定义属性 *定义* 行(token 本体),如 `  --accent: #3FE0D0;`
// 这是唯一允许出现裸色的 CSS 行;.tsx 里不会有这种行,所以组件文件无豁免。
const TOKEN_DEF_RE = /^\s*--[\w-]+\s*:/;

let failures = [];
for (const rel of CHECKED) {
  const text = readFileSync(resolve(ROOT, rel), "utf8");
  let ignoring = false;
  text.split("\n").forEach((line, i) => {
    // 豁免 ②:多行品牌/调色板常量用区间标记包起来(TILE / AVATAR_PALETTE / PALETTE)
    if (line.includes("theme-guard:ignore-start")) { ignoring = true; return; }
    if (line.includes("theme-guard:ignore-end")) { ignoring = false; return; }
    if (ignoring) return;
    if (line.includes("theme-guard:ignore")) return;   // 单行豁免
    if (TOKEN_DEF_RE.test(line)) return;                // token 定义行
    if (COLOR_RE.test(line)) failures.push(`${rel}:${i + 1}  ${line.trim()}`);
  });
}

if (failures.length) {
  console.error(`✗ 发现 ${failures.length} 处未迁移裸色:`);
  failures.forEach(f => console.error("  " + f));
  process.exit(1);
}
console.log(`✓ ${CHECKED.length} 个文件无残留裸色`);
