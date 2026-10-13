// frontend/scripts/bump-fontsizes.mjs
// 抬高字号下限:用颜色做层级,不再靠缩小字号。一次确定性映射,可重跑。
// 映射(只动"小字"≤13;标题 ≥14 不动):
//   9/10/11 → 13   (表头/面包屑/chip/状态/时间戳等 meta)
//   12/13   → 14   (次要正文/字段值/正文,= 主内容字号)
// 想整体再调:改 MAP 重跑 `node scripts/bump-fontsizes.mjs`。
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");
const MAP = { 9: 13, 10: 13, 11: 13, 12: 14, 13: 14 };

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) out.push(...walk(p));
    else if (/\.(tsx?|css)$/.test(name)) out.push(p);
  }
  return out;
}

let total = 0;
const remap = (n) => (MAP[+n] != null ? (total++, MAP[+n]) : n);

for (const f of walk(SRC)) {
  let s = readFileSync(f, "utf8");
  const before = s;
  // React 行内 style:  fontSize: 11        →  fontSize: 13
  s = s.replace(/fontSize:(\s*)(\d+)\b/g, (m, sp, n) => `fontSize:${sp}${remap(n)}`);
  // JSX 数值 prop(recharts/SVG):  fontSize={11}  →  fontSize={13}
  s = s.replace(/fontSize=\{(\d+)\}/g, (m, n) => `fontSize={${remap(n)}}`);
  // CSS:  font-size: 11px  →  font-size: 13px
  s = s.replace(/font-size:(\s*)(\d+)px/g, (m, sp, n) => `font-size:${sp}${remap(n)}px`);
  if (s !== before) writeFileSync(f, s);
}
console.log(`bumped ${total} font-size values`);
