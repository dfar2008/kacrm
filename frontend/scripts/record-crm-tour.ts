/**
 * CRM 界面导览视频录制器（设计稿:docs/superpowers/specs/2026-07-05-crm-ui-tour-video-design.md）。
 *
 * 用系统 Chrome + playwright-core 在真实 dev 环境自动走一遍 CRM 界面并录屏:
 *   准备演示数据(客户/商机/四个自定义视图/一条预埋审批) → 九章导览 →
 *   输出 webm(+ffmpeg 转 mp4)、双语 VTT 章节字幕、poster 截图 → 清理演示数据。
 *
 * 预埋审批用一个没有注册 executor 的 egress kind(quote.send):dispatcher 的
 * _execute_action 对未知 kind 直接标记 executed,镜头里点「✓ 批准」状态干净
 * 翻转,不产生任何真实副作用。
 *
 * 运行(先起 dev 后端 8765 + Vite,默认 5174):
 *   cd frontend && bun run scripts/record-crm-tour.ts            # 正式录制
 *   cd frontend && DRY=1 bun run scripts/record-crm-tour.ts      # 干跑:不录像,逐章截图到 OUT/dry/
 * 环境变量:BASE(默认 http://localhost:5174)、OUT(默认 public/tour)、
 *   CHROME(默认系统 Chrome)、REPO_PY(默认 <repo>/.venv/bin/python,预埋审批用)。
 */
import { chromium, type BrowserContext, type Locator, type Page } from "playwright-core";
import { existsSync, mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const BASE = process.env.BASE ?? "http://localhost:5174";
const OUT = resolve(import.meta.dir, "..", process.env.OUT ?? "public/tour");
const DRY = !!process.env.DRY;
const CHROME = process.env.CHROME
  ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const REPO_ROOT = resolve(import.meta.dir, "..", "..");
const REPO_PY = process.env.REPO_PY ?? join(REPO_ROOT, ".venv", "bin", "python");
// python 的工作目录须是 .venv 所在检出(worktree 无 venv 时经 REPO_PY 指向主仓)
const PY_CWD = resolve(dirname(REPO_PY), "..", "..");
const VIEW = { width: 1920, height: 1080 };
const TAG = "KACRMTOUR"; // 演示数据标记,清理时按它兜底

// ─── 章节剧本(字幕双语;时长由交互自然决定) ────────────────────────────
interface Chapter { key: string; zh: string; en: string; }
const CHAPTERS: Chapter[] = [
  { key: "intro", zh: "① 客户列表 — 搜索、业务分段、我的/团队/全部一目了然",
    en: "1. Account list — search, segments and my/team/all scopes" },
  { key: "listcfg", zh: "② 列表字段自己定 — 两行布局,想看什么放什么",
    en: "2. Customize list fields — two-row layout, show what matters" },
  { key: "views", zh: "③ 同一份数据,五种视图秒切:表格 / 卡片 / 看板(可拖) / 时间线 / 日历",
    en: "3. One dataset, five views: table / cards / kanban (drag!) / timeline / calendar" },
  { key: "headline", zh: "④ 详情页顶部摘要芯片,管理员可自定义关键字段",
    en: "4. Record headline chips — admins pick the key fields" },
  { key: "allinfo", zh: "⑤ 全部字段一屏尽览:1/2 列切换、点击即改、🤖 让 Copilot 改",
    en: "5. All fields in one drawer: 1/2-column grid, click-to-edit, ask Copilot" },
  { key: "resize", zh: "⑥ 面板宽度随手拖,双击复位 — 布局跟着你的习惯走",
    en: "6. Drag panel widths, double-click to reset — layout follows you" },
  { key: "copilot", zh: "⑦ 每条记录自带 Copilot:自动摘要 + 建议提问,一句话驱动 CRM",
    en: "7. Per-record Copilot: auto summary + suggested questions" },
  { key: "approvals", zh: "⑧ 关键动作先审批 — AI 干活,人拍板",
    en: "8. Sensitive actions await approval — AI works, humans decide" },
  { key: "outro", zh: "对话之外,还有一整套顺手的界面 — 进入 CRM 试试吧",
    en: "Beyond chat: a full, polished UI — come try the CRM" },
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ─── 虚拟光标(无头浏览器没有系统指针,观众需要一个"手") ─────────────────
const CURSOR_INIT = `(() => {
  if (window.__tourCursorInstalled) return; window.__tourCursorInstalled = true;
  const mk = () => {
    if (!document.body || document.getElementById('__tour_cursor')) return;
    const d = document.createElement('div'); d.id = '__tour_cursor';
    d.style.cssText = 'position:fixed;left:-40px;top:-40px;width:20px;height:20px;' +
      'border-radius:50%;background:rgba(63,224,208,.30);border:2px solid rgba(63,224,208,.95);' +
      'box-shadow:0 0 10px rgba(63,224,208,.75);z-index:2147483647;pointer-events:none;' +
      'transform:translate(-50%,-50%)';
    document.body.appendChild(d);
  };
  window.__tourCursor = {
    move(x, y) { mk(); const d = document.getElementById('__tour_cursor');
      if (d) { d.style.left = x + 'px'; d.style.top = y + 'px'; } },
    pulse() { mk(); const d = document.getElementById('__tour_cursor'); if (!d) return;
      d.animate([{ transform: 'translate(-50%,-50%) scale(1)' },
                 { transform: 'translate(-50%,-50%) scale(1.7)' },
                 { transform: 'translate(-50%,-50%) scale(1)' }], { duration: 300 }); },
  };
  if (document.readyState !== 'loading') mk();
  else document.addEventListener('DOMContentLoaded', mk);
})();`;

class Tour {
  page: Page;
  pos = { x: 40, y: 40 };
  marks: Array<{ key: string; at: number }> = [];
  t0 = 0;
  constructor(page: Page) { this.page = page; }

  mark(key: string) {
    if (!this.t0) this.t0 = Date.now();
    this.marks.push({ key, at: Date.now() });
    console.log(`  ▸ chapter ${key} @ ${((Date.now() - this.t0) / 1000).toFixed(1)}s`);
  }

  /** 光标平滑移动到 (x,y) 并同步真实 mouse.move。 */
  async moveTo(x: number, y: number, ms = 550) {
    const steps = Math.max(8, Math.round(ms / 40));
    const { x: sx, y: sy } = this.pos;
    for (let i = 1; i <= steps; i++) {
      // ease-in-out
      const t = i / steps, e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      const nx = sx + (x - sx) * e, ny = sy + (y - sy) * e;
      await this.page.evaluate(
        ([cx, cy]) => (window as any).__tourCursor?.move(cx, cy), [nx, ny]);
      await this.page.mouse.move(nx, ny);
      await sleep(ms / steps);
    }
    this.pos = { x, y };
  }

  async center(loc: Locator): Promise<{ x: number; y: number }> {
    // scrollIntoViewIfNeeded 要求元素"稳定",列表里有每秒重渲的相对时间等
    // 会让它永远等下去——超时后退化为直接 scrollIntoView + 读 bbox。
    try { await loc.scrollIntoViewIfNeeded({ timeout: 2500 }); }
    catch {
      await loc.evaluate((el) => el.scrollIntoView({ block: "center" })).catch(() => {});
    }
    await sleep(250);
    const b = await loc.boundingBox();
    if (!b) throw new Error("boundingBox null for locator");
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  }

  async glide(loc: Locator, ms = 550) {
    const c = await this.center(loc);
    await this.moveTo(c.x, c.y, ms);
  }

  async click(loc: Locator, opts: { settle?: number } = {}) {
    await this.glide(loc);
    // 前一次点击可能触发重排(视图条 chips 会挪位),按下前重测一次
    const c2 = await this.center(loc);
    if (Math.hypot(c2.x - this.pos.x, c2.y - this.pos.y) > 4) {
      await this.moveTo(c2.x, c2.y, 200);
    }
    await this.page.evaluate(() => (window as any).__tourCursor?.pulse());
    await this.page.mouse.down(); await sleep(70); await this.page.mouse.up();
    await sleep(opts.settle ?? 700);
  }

  async clickXY(x: number, y: number, settle = 700) {
    await this.moveTo(x, y);
    await this.page.evaluate(() => (window as any).__tourCursor?.pulse());
    await this.page.mouse.down(); await sleep(70); await this.page.mouse.up();
    await sleep(settle);
  }

  /** HTML5 DnD:mousedown → 分步 move(Chromium 会翻译成 drag 事件) → mouseup。 */
  async drag(from: Locator, to: Locator) {
    const a = await this.center(from);
    await this.moveTo(a.x, a.y);
    await this.page.mouse.down();
    await sleep(250);
    const b = await this.center(to);
    const steps = 22;
    for (let i = 1; i <= steps; i++) {
      const nx = a.x + (b.x - a.x) * (i / steps), ny = a.y + (b.y - a.y) * (i / steps);
      await this.page.mouse.move(nx, ny, { steps: 1 });
      await this.page.evaluate(
        ([cx, cy]) => (window as any).__tourCursor?.move(cx, cy), [nx, ny]);
      await sleep(38);
    }
    await sleep(250);
    await this.page.mouse.up();
    this.pos = b;
    await sleep(800);
  }

  async type(text: string, delay = 55) { await this.page.keyboard.type(text, { delay }); }

  async shot(name: string) {
    if (!DRY) return;
    mkdirSync(join(OUT, "dry"), { recursive: true });
    await this.page.screenshot({ path: join(OUT, "dry", `${name}.png`) });
  }
}

// ─── 预埋/清理审批行(经 repo venv python 直插 gateway_actions) ──────────
function seedApproval(): string {
  const id = crypto.randomUUID();
  const py = `
from hermes.crm.db import db_session
import json
with db_session() as db:
    db.execute(
        """INSERT INTO gateway_actions
           (id, profile_id, agent_name, kind, target, params, state, trust_level, origin, created_at)
           VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,'pending_approval',2,%s::jsonb, now())""",
        ("${id}", "admin", "agent:kacrm", "quote.send",
         json.dumps({"module": "quotes"}),
         json.dumps({"op": "send_quote", "data": {
             "客户": "杭州云帆科技", "报价单": "QT-2026-0705",
             "金额": "¥580,000", "备注": "含一年质保与实施服务"}}),
         json.dumps({"channel": "webui"})))
print("seeded")
`;
  const r = Bun.spawnSync([REPO_PY, "-c", py], { cwd: PY_CWD });
  const out = r.stdout.toString() + r.stderr.toString();
  if (!out.includes("seeded")) throw new Error(`approval seed failed: ${out.slice(-400)}`);
  console.log(`  seeded approval ${id}`);
  return id;
}

function cleanupApproval(id: string) {
  const py = `
from hermes.crm.db import db_session
with db_session() as db:
    db.execute("DELETE FROM gateway_actions WHERE id = %s", ("${id}",))
print("cleaned")
`;
  const r = Bun.spawnSync([REPO_PY, "-c", py], { cwd: PY_CWD });
  if (!(r.stdout.toString() + r.stderr.toString()).includes("cleaned")) {
    console.warn("  WARN: approval cleanup failed — remove row manually:", id);
  }
}

// ─── 页面内 API 助手(同源 cookie,经 Vite 代理打到 8765) ────────────────
async function api(page: Page, method: string, url: string, body?: unknown) {
  return await page.evaluate(async ([m, u, b]) => {
    const r = await fetch(u as string, {
      method: m as string,
      headers: { "Content-Type": "application/json" },
      body: b == null ? undefined : JSON.stringify(b),
    });
    const text = await r.text();
    let json: any = null; try { json = JSON.parse(text); } catch { /* keep text */ }
    return { status: r.status, json, text: text.slice(0, 300) };
  }, [method, url, body ?? null]);
}

interface Seeded {
  accountIds: number[]; potentialIds: number[]; viewIds: number[]; approvalId: string;
}

async function seedData(page: Page): Promise<Seeded> {
  console.log("[seed] demo data…");
  const login = await api(page, "POST", "/crm/auth/login",
    { username: "admin", password: "admin123" });
  if (login.status !== 200) throw new Error(`login failed ${login.status}`);

  const accounts = [
    { accounts_name: "杭州云帆科技", email: "biz@yunfan.tech", website: "https://yunfan.tech", mobile: "13800001234", description: "云计算服务商,员工 200+,去年完成 B 轮" },
    { accounts_name: "南京星辰贸易", email: "hi@xingchen.trade", mobile: "13900005678", description: "进出口贸易,合作三年的老客户" },
    { accounts_name: "深圳蓝海智造", email: "sales@lanhai.mfg", mobile: "13700009012", description: "智能制造,新客户" },
    { accounts_name: "北京极光软件", email: "bd@aurora.soft", mobile: "13600003456", description: "SaaS 服务商,渠道伙伴" },
  ];
  const accountIds: number[] = [];
  for (const a of accounts) {
    const r = await api(page, "POST", "/crm/records/accounts", a);
    if (r.status !== 200 || !r.json?.id) throw new Error(`account seed failed: ${r.status} ${r.text}`);
    accountIds.push(r.json.id);
  }

  const day = (offset: number) => {
    const d = new Date(Date.now() + offset * 86400_000);
    return d.toISOString().slice(0, 10);
  };
  const pots = [
    { potentials_name: "云帆科技 · 私有化部署一期", amount: 1200000, milestone: "negotiation", accounts_id: accountIds[0], closingdate: day(12), probability: 70 },
    { potentials_name: "云帆科技 · 年度技术支持", amount: 300000, milestone: "proposal_sent", accounts_id: accountIds[0], closingdate: day(20), probability: 55 },
    { potentials_name: "星辰贸易 · 供应链协同平台", amount: 860000, milestone: "needs_analysis", accounts_id: accountIds[1], closingdate: day(30), probability: 40 },
    { potentials_name: "星辰贸易 · 数据看板扩容", amount: 150000, milestone: "initial_contact", accounts_id: accountIds[1], closingdate: day(45), probability: 20 },
    { potentials_name: "蓝海智造 · MES 对接项目", amount: 980000, milestone: "proposal_sent", accounts_id: accountIds[2], closingdate: day(18), probability: 60 },
    { potentials_name: "蓝海智造 · 设备巡检 AI", amount: 450000, milestone: "needs_analysis", accounts_id: accountIds[2], closingdate: day(38), probability: 35 },
    { potentials_name: "极光软件 · 联合解决方案", amount: 520000, milestone: "closing", accounts_id: accountIds[3], closingdate: day(6), probability: 85 },
    { potentials_name: "极光软件 · 培训服务包", amount: 90000, milestone: "won", accounts_id: accountIds[3], closingdate: day(-3), probability: 100 },
  ];
  const potentialIds: number[] = [];
  for (const [i, p] of pots.entries()) {
    const r = await api(page, "POST", "/crm/records/potentials",
      { ...p, potentialno: `${TAG}-P${i + 1}`, description: "重点跟进,客户预算已确认" });
    if (r.status !== 200 || !r.json?.id) throw new Error(`potential seed failed: ${r.status} ${r.text}`);
    potentialIds.push(r.json.id);
  }

  // 四个自定义视图:filters 把演示数据之外的记录滤掉(amount>0 即演示商机)
  const demoFilter = [{ field: "potentialno", operator: "contains", value: TAG }];
  const views = [
    { name: "卡片视图", type: "card", filters: demoFilter, view_config: { card_per_row: 3, card_fields: ["amount", "milestone", "closingdate"] } },
    { name: "商机看板", type: "kanban", filters: demoFilter, view_config: { group_by: "milestone" } },
    { name: "时间线视图", type: "timeline", filters: demoFilter, view_config: { date_field: "closingdate", content_fields: ["amount", "milestone"] } },
    { name: "日历视图", type: "calendar", filters: demoFilter, view_config: { date_field: "closingdate" } },
  ];
  const viewIds: number[] = [];
  for (const v of views) {
    const r = await api(page, "POST", "/crm/modules/potentials/views", v);
    const vid = r.json?.view?.id;
    if (r.status !== 200 || !vid) throw new Error(`view seed failed: ${r.status} ${r.text}`);
    viewIds.push(vid);
  }
  console.log(`  accounts=${accountIds} potentials=${potentialIds.length} views=${viewIds}`);

  // 预热 profile 网关(模型连接/技能水合):这 20s 发生在会被裁掉的预滚段。
  // 用巡演中从不打开的最后一条记录,避免暖场对话出现在镜头里。
  console.log("  warming up copilot gateway…");
  const warm = await api(page, "POST", "/api/copilot/crm/send", {
    module: "potentials", record_id: potentialIds[potentialIds.length - 1],
    message: "用一句话说明这条商机当前状态",
  });
  console.log(`  warm-up: ${warm.status} ${String(warm.json?.reply ?? warm.text).slice(0, 60)}`);

  return { accountIds, potentialIds, viewIds, approvalId: "" };
}

async function cleanupData(page: Page, s: Seeded) {
  console.log("[cleanup] demo data…");
  for (const id of s.viewIds) {
    await api(page, "DELETE", `/crm/modules/potentials/views/${id}`);
  }
  for (const id of s.potentialIds) await api(page, "DELETE", `/crm/records/potentials/${id}`);
  for (const id of s.accountIds) await api(page, "DELETE", `/crm/records/accounts/${id}`);
}

// ─── SPA 内路由跳转(pushState,避免整页刷新丢 overlay) ──────────────────
async function go(page: Page, path: string, settle = 1600) {
  await page.evaluate((p) => {
    history.pushState({}, "", p);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  await sleep(settle);
}

// ─── 主流程 ────────────────────────────────────────────────────────────
async function main() {
  mkdirSync(OUT, { recursive: true });
  if (!existsSync(REPO_PY)) throw new Error(`REPO_PY not found: ${REPO_PY}`);

  const approvalId = seedApproval();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx: BrowserContext = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: DRY ? 2 : 1,
    locale: "zh-CN",
    recordVideo: DRY ? undefined : { dir: OUT, size: VIEW },
  });
  await ctx.addInitScript(CURSOR_INIT);
  // dev 漂移:模型配置默认 deepseek,但 profile 激活 provider 是 minimax;
  // 预置 Copilot 面板的模型选择,避免 /crm/send 400 ModelNotInActiveList。
  await ctx.addInitScript(
    `try { localStorage.setItem("hermes.chat.model.copilot", "minimax::MiniMax-M2.7"); } catch (e) {}`);
  const page = await ctx.newPage();
  const videoStartAt = Date.now(); // 录像大致从建页开始,裁头/字幕对齐用
  page.on("dialog", (d) => void d.accept()); // 审批的 confirm()

  let seeded: Seeded | null = null;
  let tourRef: Tour | null = null;
  try {
    await page.goto(`${BASE}/app/`, { waitUntil: "domcontentloaded" });
    seeded = await seedData(page);
    seeded.approvalId = approvalId;
    await page.evaluate(() => document.fonts.ready);

    const tour = new Tour(page);
    tourRef = tour;

    // ① 客户列表:搜索/分段/范围。登录发生在初始页加载之后,SPA 还停在
    // 登录态之外——第一跳必须整页 goto 让应用带 cookie 重新初始化。
    await page.goto(`${BASE}/app/crm/accounts`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=杭州云帆科技", { timeout: 20000 });
    await sleep(1200);
    tour.mark("intro");
    await tour.glide(page.getByText("杭州云帆科技").first(), 800);
    await sleep(900);
    const scopeAll = page.getByRole("button", { name: /^全部$/ }).first();
    if (await scopeAll.count()) {
      await tour.click(scopeAll, { settle: 1000 });
      const scopeMine = page.getByRole("button", { name: /^我的$/ }).first();
      if (await scopeMine.count()) await tour.click(scopeMine, { settle: 1000 });
    }
    const search = page.locator("input[placeholder*='搜索']").first();
    await tour.click(search, { settle: 300 });
    await tour.type("云帆");
    await sleep(1400);
    await tour.shot("01-search");
    // 清空搜索恢复列表
    await page.keyboard.press("Meta+a"); await page.keyboard.press("Backspace");
    await sleep(900);

    // ② 列表字段配置(默认表格视图下)
    await go(page, "/app/crm/potentials", 2200);
    tour.mark("listcfg");
    await tour.click(page.locator("button[title='自定义列表字段']").first(), { settle: 1400 });
    await tour.shot("02a-listcfg");
    // 从候选里点一个字段加入,再上移一次,保存
    const candidate = page.getByText("预计成交日期").first();
    if (await candidate.count()) await tour.click(candidate, { settle: 800 });
    const up = page.locator("button[title='上移']").last();
    if (await up.count()) await tour.click(up, { settle: 700 });
    const saveBtn = page.getByRole("button", { name: /^保存$/ }).first();
    if (await saveBtn.count()) await tour.click(saveBtn, { settle: 1600 });
    await tour.shot("02b-listcfg-saved");

    // ③ 五种视图 + 看板拖拽。视图渲染在左侧列表栏里——先把列表栏拖宽,
    // 看板/日历才有施展空间(这本身也是真实用法)。
    tour.mark("views");
    const listHandle = page.locator("div[style*='col-resize']").first();
    if (await listHandle.count()) {
      const hc = await tour.center(listHandle);
      await tour.moveTo(hc.x, hc.y, 500);
      await page.mouse.down();
      await tour.moveTo(hc.x + 560, hc.y, 900);
      await page.mouse.up();
      await sleep(900);
    }
    await tour.click(page.getByRole("button", { name: "卡片视图", exact: true }).first(), { settle: 1800 });
    await tour.shot("03a-card");
    await tour.click(page.getByRole("button", { name: "商机看板", exact: true }).first(), { settle: 800 });
    await page.waitForSelector("[draggable]", { timeout: 8000 }); // 看板必须真的激活
    await sleep(1200);
    await tour.shot("03b-kanban");
    if (!DRY) await page.screenshot({ path: join(OUT, "poster-src.png") });
    // 拖一张卡:星辰贸易 · 供应链协同平台(需求分析)→「已发方案」列
    const card = page.locator("[draggable]", { hasText: "供应链协同平台" }).first();
    const targetCol = page.getByText("已发方案", { exact: true }).first();
    if (await card.count() && await targetCol.count()) {
      await tour.drag(card, targetCol);
      await tour.shot("03c-dragged");
      if (DRY) { // 干跑时校验拖拽真的写回了 milestone
        const potId = seeded!.potentialIds[2];
        const chk = await api(page, "GET", `/crm/records/potentials?view_id=${seeded!.viewIds[1]}&limit=20`);
        const row = (chk.json?.data ?? []).find((r: any) => r.potentials_id === potId || r.id === potId);
        console.log(`  drag check: milestone=${row?.milestone} (want proposal_sent)`);
      }
    } else {
      console.warn("  WARN: kanban drag skipped — card or column not found");
    }
    await tour.click(page.getByRole("button", { name: "时间线视图", exact: true }).first(), { settle: 1700 });
    await tour.shot("03d-timeline");
    await tour.click(page.getByRole("button", { name: "日历视图", exact: true }).first(), { settle: 1700 });
    await tour.shot("03e-calendar");
    // 「+ 新建视图」快闪:让观众看到 5 个类型按钮,然后关闭
    const newViewBtn = page.getByRole("button", { name: /新建视图/ }).first();
    if (await newViewBtn.count()) {
      await tour.click(newViewBtn, { settle: 1200 });
      await tour.shot("03f-viewbuilder");
      await sleep(1200);
      await page.keyboard.press("Escape");
      await sleep(600);
    }
    // 回卡片视图,给第④章一个可点的记录卡
    await tour.click(page.getByRole("button", { name: "卡片视图", exact: true }).first(), { settle: 1500 });

    // ④ 详情摘要芯片(先双击手柄把列表栏复位,给详情面板腾地方)
    const resetHandle = page.locator("div[style*='col-resize']").first();
    if (await resetHandle.count()) {
      const rc = await tour.center(resetHandle);
      await tour.moveTo(rc.x, rc.y, 400);
      await page.mouse.dblclick(rc.x, rc.y);
      await sleep(900);
    }
    await tour.click(page.getByText("云帆科技 · 私有化部署一期").first(), { settle: 2000 });
    tour.mark("headline");
    const gear = page.locator("button[title*='自定义顶部展示']").first();
    await tour.click(gear, { settle: 1300 });
    await tour.shot("04a-headline");
    const hCand = page.getByText("成功概率").first();
    if (await hCand.count()) await tour.click(hCand, { settle: 700 });
    const hSave = page.getByRole("button", { name: /^保存$/ }).first();
    if (await hSave.count()) await tour.click(hSave, { settle: 1500 });
    await tour.shot("04b-headline-saved");

    // ⑤ 全部信息抽屉:列切换 + 点击即改 + 🤖
    tour.mark("allinfo");
    await tour.click(page.locator("button[title^='全部字段']").first(), { settle: 1600 });
    await tour.shot("05a-drawer");
    const colsBtn = page.locator("button[title*='切换每行字段数']").first();
    await tour.click(colsBtn, { settle: 1200 });   // 1 → 2 列
    await tour.shot("05b-2cols");
    await tour.click(colsBtn, { settle: 1000 });   // 回 1 列
    // 点击即改:改「成功概率」这类数字字段
    const probRow = page.getByText(/^成功概率|probability/i).first();
    if (await probRow.count()) {
      const row = probRow.locator("xpath=ancestor::*[position()<=3]").last();
      await tour.click(row.getByText(/\d+|—|-/).first().or(row), { settle: 600 });
      const input = page.locator("input:focus, textarea:focus").first();
      if (await input.count()) {
        await page.keyboard.press("Meta+a");
        await tour.type("80");
        await page.keyboard.press("Enter");
        await sleep(1200);
      }
    }
    // 🤖 让 Copilot 改
    const robot = page.locator("button[title*='让 Copilot 改']").first();
    if (await robot.count()) {
      await tour.click(robot, { settle: 1500 });
      await tour.shot("05c-copilot-prefill");
      // 只演示"预填",不真发——清空输入框,防止残留文本拼进后面的提问
      const prefillBox = page.locator("textarea[placeholder*='问 Copilot']").first();
      if (await prefillBox.count()) {
        await prefillBox.click();
        await page.keyboard.press("Meta+a");
        await page.keyboard.press("Backspace");
        await sleep(400);
      }
    }
    // 关抽屉
    const closeDrawer = page.locator("button[title='关闭']").first();
    if (await closeDrawer.count()) await tour.click(closeDrawer, { settle: 900 });
    else await page.keyboard.press("Escape");

    // ⑥ 面板宽度拖拽
    tour.mark("resize");
    const handles = page.locator("div[style*='col-resize']");
    const n = await handles.count();
    if (n >= 1) {
      const h1 = handles.first();
      const c1 = await tour.center(h1);
      await tour.moveTo(c1.x, c1.y);
      await page.mouse.down();
      await tour.moveTo(c1.x - 180, c1.y, 700);
      await sleep(250);
      await tour.moveTo(c1.x + 120, c1.y, 600);
      await page.mouse.up();
      await sleep(600);
      // 双击复位
      await page.mouse.dblclick(c1.x + 120, c1.y);
      await sleep(900);
    }
    if (n >= 2) {
      const h2 = handles.nth(1);
      const c2 = await tour.center(h2);
      await tour.moveTo(c2.x, c2.y);
      await page.mouse.down();
      await tour.moveTo(c2.x - 200, c2.y, 700);
      await page.mouse.up();
      await sleep(700);
      await page.mouse.dblclick(c2.x - 200, c2.y);
      await sleep(900);
    }
    await tour.shot("06-resize");

    // ⑦ Copilot:等 InsightCard 的建议提问(真实 LLM 生成,可能要几十秒);
    // 超时则退化为手动在输入框问一句——两条路都是真实调用。
    tour.mark("copilot");
    // 建议提问 chips 是真实 LLM 生成的,掠过展示即可;真正发问用一条
    // 已实测稳定的摘要类问题(chip 的问题可能触发慢工具而超时)。
    const chipBtn = page.locator("button").filter({ hasText: /^→/ }).first();
    if (await chipBtn.waitFor({ timeout: 30000 }).then(() => true).catch(() => false)) {
      await tour.glide(chipBtn, 700);
      await sleep(1500);
    }
    // 模型选择用 init-script 预置的 minimax::MiniMax-M2.7(清单里有两个同名
    // "MiniMax M2.7",另一个是缺钥匙的 opencode-zen 条目——镜头里不做重选,
    // 免得点错 401)。
    const askOnce = async () => {
      const box = page.locator("textarea[placeholder*='问 Copilot']").first();
      if (!(await box.count())) return false;
      await tour.click(box, { settle: 300 });
      await page.keyboard.press("Meta+a");
      await page.keyboard.press("Backspace"); // 清掉任何残留再问
      await tour.type("总结这条商机的关键信息，并给出下一步建议", 55);
      await page.keyboard.press("Meta+Enter");
      return true;
    };
    const fallbackCount = () => page.evaluate(
      () => (document.body.innerText.match(/暂时没法直接处理|HTTP 40\d|Missing API key/g) ?? []).length);
    const before = await fallbackCount();
    if (await askOnce()) {
      // 等真实回复:成功的回复气泡带「· 用 <model>」元信息;失败出 fallback/401。
      const replyCount = () => page.evaluate(
        () => (document.body.innerText.match(/· 用 /g) ?? []).length);
      const replies0 = await replyCount();
      let ok = false;
      for (let i = 0; i < 35; i++) {
        await sleep(2000);
        if ((await fallbackCount()) > before) break;         // 失败,走重试
        if ((await replyCount()) > replies0) { ok = true; break; }
      }
      if (!ok && (await fallbackCount()) > before) {
        console.warn("  copilot fallback — retrying once");
        await sleep(1500);
        await askOnce();
        for (let i = 0; i < 35; i++) {
          await sleep(2000);
          if ((await replyCount()) > replies0) { ok = true; break; }
        }
      }
      console.log(`  copilot reply ok=${ok}`);
      await sleep(6000); // 停一停,让观众读完回复
      await tour.shot("07-copilot");
    } else {
      console.warn("  WARN: copilot ask skipped");
    }

    // ⑧ 审批中心:批准预埋的报价单发送
    await go(page, "/app/approvals", 2200);
    tour.mark("approvals");
    await tour.glide(page.getByText("QT-2026-0705").first(), 700).catch(() => {});
    await sleep(800);
    const approveBtn = page.getByRole("button", { name: /批准/ }).first();
    if (await approveBtn.count()) {
      await tour.click(approveBtn, { settle: 2200 }); // confirm() 已自动接受
      await tour.shot("08-approved");
    }

    // ⑨ 收尾:回看板,停留给 CTA 字幕
    await go(page, "/app/crm/potentials", 1800);
    const kanbanTab = page.getByRole("button", { name: "商机看板", exact: true }).first();
    if (await kanbanTab.count()) await tour.click(kanbanTab, { settle: 1500 });
    tour.mark("outro");
    await sleep(4000);

    // ─ 输出字幕(相对 t0;视频头部到 t0 之间是登录加载,无字幕) ─
    const LEAD_MS = 1000; // 裁头后保留 1s 预滚,字幕整体后移这一秒
    const vtt = (lang: "zh" | "en") => {
      const fmt = (ms: number) => {
        const s = Math.max(0, ms) / 1000;
        const hh = String(Math.floor(s / 3600)).padStart(2, "0");
        const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
        const ss = (s % 60).toFixed(3).padStart(6, "0");
        return `${hh}:${mm}:${ss}`;
      };
      let out = "WEBVTT\n\n";
      tour.marks.forEach((m, i) => {
        const ch = CHAPTERS.find((c) => c.key === m.key)!;
        const start = m.at - tour.t0 + LEAD_MS;
        const end = i + 1 < tour.marks.length
          ? tour.marks[i + 1].at - tour.t0 + LEAD_MS
          : start + 6000;
        out += `${fmt(start)} --> ${fmt(end)}\n${ch[lang]}\n\n`;
      });
      return out;
    };
    writeFileSync(join(OUT, "crm-tour.zh.vtt"), vtt("zh"));
    writeFileSync(join(OUT, "crm-tour.en.vtt"), vtt("en"));
    console.log("[out] VTT written");
  } finally {
    // 先清数据(仍在录像内但只有 API 调用,画面静止在看板上,可被 ffmpeg 截尾)
    if (seeded) await cleanupData(page, seeded).catch((e) => console.warn("cleanup:", e));
    const video = page.video();
    await ctx.close();
    await browser.close();
    cleanupApproval(approvalId);
    if (!DRY && video) {
      const raw = await video.path();
      const rawWebm = join(OUT, "crm-tour.raw.webm");
      renameSync(raw, rawWebm);
      // 裁掉章节一之前的预滚(登录+种数据),保留 1s 起手;结尾留 6s CTA。
      const headSec = Math.max(0, (tourRef!.t0 - videoStartAt) / 1000 - 1);
      const lastAt = tourRef!.marks[tourRef!.marks.length - 1]?.at ?? tourRef!.t0;
      const durSec = (lastAt - tourRef!.t0) / 1000 + 1 + 6;
      const ffmpeg = Bun.which("ffmpeg");
      if (ffmpeg) {
        const mp4 = join(OUT, "crm-tour.mp4");
        const r = Bun.spawnSync([
          ffmpeg, "-y", "-ss", headSec.toFixed(2), "-i", rawWebm,
          "-t", durSec.toFixed(2),
          "-c:v", "libx264", "-crf", "23", "-preset", "medium",
          "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", mp4,
        ]);
        if (r.exitCode !== 0) {
          console.error("ffmpeg failed:", r.stderr.toString().slice(-500));
        } else {
          console.log("[out] video:", mp4);
          Bun.spawnSync(["rm", rawWebm]);
        }
        const posterSrc = join(OUT, "poster-src.png");
        if (existsSync(posterSrc)) {
          Bun.spawnSync([ffmpeg, "-y", "-i", posterSrc, "-qscale:v", "3",
                         join(OUT, "poster.jpg")]);
          Bun.spawnSync(["rm", posterSrc]);
          console.log("[out] poster:", join(OUT, "poster.jpg"));
        }
      } else {
        console.warn("WARN: ffmpeg not found — raw webm kept:", rawWebm);
      }
    }
  }
}

await main();
console.log("DONE");
