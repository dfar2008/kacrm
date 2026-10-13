/**
 * 向导页内嵌演示 v2:七幕教学导览的剧本与播放状态机(纯函数,组件只负责渲染与计时)。
 *
 * 一个 tick = TICK_MS。user 行逐字显现(typing);peer(客户来消息)/agent 行整条
 * 出现;行完整后停 holdTicks 个 tick 再进下一行;幕内行尽进下一幕,末幕循环。
 * 文本长度由渲染层解析 i18n 后经 lengths[act][line] 传入,状态机与语言无关。
 * 设计定稿见 docs/superpowers/specs/2026-07-05-onboarding-demo-teaching-reel.md。
 */

export type DemoRole = "user" | "peer" | "agent";

export type DemoCardKind =
  | "fields"       // 建档结果:字段行列表
  | "field-grow"   // 普通字段长出:新字段芯片高亮
  | "ai-research"  // AI 字段联网调研结果:置信度圆点 + 来源行
  | "menu-grow"    // 新模块出现在左侧菜单:末项高亮
  | "workflow"     // 自动化规则:当…则… + 审批注脚
  | "chart"        // 迷你柱状图:rowKeys 为横轴标签,bars 为柱高(%)
  | "digest";      // 晨报清单

export interface DemoCard {
  kind: DemoCardKind;
  rowKeys: string[];    // 卡片各行的 i18n key(chart 时为横轴标签)
  noteKey?: string;     // 底部注脚(来源/审批提示/图例)
  confidence?: number;  // ai-research:置信度圆点实心数(满 4)
  bars?: number[];      // chart:柱高百分比,与 rowKeys 对齐
}

export interface DemoLine {
  role: DemoRole;
  textKey: string;      // 气泡文本的 i18n key
  badgeKey?: string;    // 气泡上方渠道徽标(微信来消息/飞书主动推送)
  attachKey?: string;   // user 行的附件缩略(上传发票照片等):i18n key = 文件名
  card?: DemoCard;      // agent 回复卡片
  holdTicks: number;    // 完整显示后停留的 tick 数
}

export interface DemoAct {
  titleKey: string;     // 章节字幕「第 N 步 · …」
  lines: DemoLine[];
}

export interface DemoState {
  act: number;   // 当前幕
  line: number;  // 幕内当前行
  chars: number; // user 行已显现字符数
  hold: number;  // 完整显示后已停留 tick 数
}

export const INITIAL_STATE: DemoState = { act: 0, line: 0, chars: 0, hold: 0 };
export const TICK_MS = 80;

export const ACTS: DemoAct[] = [
  { // 1 一条线索,转成客户和商机(CRM 漏斗入口:线索→客户→商机)
    titleKey: "setup.demo_act1_title",
    lines: [
      { role: "user", textKey: "setup.demo_act1_user", holdTicks: 10 },
      { role: "agent", textKey: "setup.demo_act1_agent", holdTicks: 34,
        card: { kind: "fields", rowKeys: [
          "setup.demo_act1_r1", "setup.demo_act1_r2", "setup.demo_act1_r3",
        ] } },
    ],
  },
  { // 2 说一句,长出新字段(对话即定制)
    titleKey: "setup.demo_act2_title",
    lines: [
      { role: "user", textKey: "setup.demo_act2_user", holdTicks: 8 },
      { role: "agent", textKey: "setup.demo_act2_agent", holdTicks: 30,
        card: { kind: "field-grow", rowKeys: [
          "setup.demo_act2_r1", "setup.demo_act2_r2",
        ] } },
    ],
  },
  { // 3 AI 字段:联网调研自动填(AI 字段/诚实边界)
    titleKey: "setup.demo_act3_title",
    lines: [
      { role: "user", textKey: "setup.demo_act3_user", holdTicks: 8 },
      { role: "agent", textKey: "setup.demo_act3_searching", holdTicks: 16 },
      { role: "agent", textKey: "setup.demo_act3_agent", holdTicks: 52,
        card: { kind: "ai-research", confidence: 3,
          rowKeys: ["setup.demo_act3_r1"], noteKey: "setup.demo_act3_note" } },
    ],
  },
  { // 4 连整个模块都能长(对话即定制重锤)
    titleKey: "setup.demo_act4_title",
    lines: [
      { role: "user", textKey: "setup.demo_act4_user", holdTicks: 8 },
      { role: "agent", textKey: "setup.demo_act4_agent", holdTicks: 38,
        card: { kind: "menu-grow", rowKeys: [
          "setup.demo_act4_m1", "setup.demo_act4_m2", "setup.demo_act4_m3",
          "setup.demo_act4_m4",
        ] } },
    ],
  },
  { // 5 拍张发票,AI 自动记账(读图录入)——上传图片,识别落成一条费用报销
    titleKey: "setup.demo_ocr_title",
    lines: [
      { role: "user", textKey: "setup.demo_ocr_user",
        attachKey: "setup.demo_ocr_attach", holdTicks: 12 },
      { role: "agent", textKey: "setup.demo_ocr_searching", holdTicks: 16 },
      { role: "agent", textKey: "setup.demo_ocr_agent", holdTicks: 48,
        card: { kind: "fields", rowKeys: [
          "setup.demo_ocr_r1", "setup.demo_ocr_r2",
          "setup.demo_ocr_r3", "setup.demo_ocr_r4",
        ], noteKey: "setup.demo_ocr_note" } },
    ],
  },
  { // 6 定个规矩,它自动执行(自动化/人监督)
    titleKey: "setup.demo_act5_title",
    lines: [
      { role: "user", textKey: "setup.demo_act5_user", holdTicks: 8 },
      { role: "agent", textKey: "setup.demo_act5_agent", holdTicks: 40,
        card: { kind: "workflow", rowKeys: [
          "setup.demo_act5_r1", "setup.demo_act5_r2",
        ], noteKey: "setup.demo_act5_note" } },
    ],
  },
  { // 7 看板也是一句话的事(AI 看板)
    titleKey: "setup.demo_act6_title",
    lines: [
      { role: "user", textKey: "setup.demo_act6_user", holdTicks: 8 },
      { role: "agent", textKey: "setup.demo_act6_agent", holdTicks: 40,
        card: { kind: "chart", rowKeys: [
          "setup.demo_act6_b1", "setup.demo_act6_b2", "setup.demo_act6_b3",
        ], bars: [45, 70, 100], noteKey: "setup.demo_act6_note" } },
    ],
  },
  { // 8 第二天早上,它已经上完班了(它会自己上班/全渠道)——无人提问,agent 主动
    titleKey: "setup.demo_act7_title",
    lines: [
      { role: "agent", textKey: "setup.demo_act7_agent", badgeKey: "setup.demo_act7_badge", holdTicks: 56,
        card: { kind: "digest", rowKeys: [
          "setup.demo_act7_r1", "setup.demo_act7_r2",
        ] } },
    ],
  },
];

export function advance(state: DemoState, lengths: number[][]): DemoState {
  const { act, line, chars, hold } = state;
  const cur = ACTS[act]?.lines[line];
  if (!cur) return INITIAL_STATE; // 越界防御:重头开始
  const full = cur.role === "user" ? (lengths[act]?.[line] ?? 0) : 0;
  if (cur.role === "user" && chars < full) {
    return { act, line, chars: chars + 1, hold: 0 };
  }
  if (hold < cur.holdTicks) {
    return { act, line, chars, hold: hold + 1 };
  }
  const nextLine = line + 1;
  if (nextLine < ACTS[act].lines.length) {
    return { act, line: nextLine, chars: 0, hold: 0 };
  }
  const nextAct = act + 1;
  if (nextAct >= ACTS.length) return INITIAL_STATE; // 循环
  return { act: nextAct, line: 0, chars: 0, hold: 0 };
}

/** 章节导航:跳到第 i 幕开头(越界钳制)。 */
export function jumpToAct(i: number): DemoState {
  const act = Math.min(Math.max(i, 0), ACTS.length - 1);
  return { act, line: 0, chars: 0, hold: 0 };
}
