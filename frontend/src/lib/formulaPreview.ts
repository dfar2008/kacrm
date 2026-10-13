/**
 * formulaPreview — 行内公式的**客户端实时预览**求值器。
 *
 * 镜像后端 ``hermes/crm/formula.py`` 的同记录算术文法(数字 / 字段引用 /
 * ``+ - * /`` / 一元负 / 比较 / ``IF ROUND MIN MAX ABS``),让子明细编辑器在
 * 用户录入时就能算出公式列的值。**后端是权威**:保存时由 formula 引擎用
 * Decimal 重算并落库;本预览只为即时反馈,解析/求值失败时返回 null,调用方
 * 回退到已存值。
 *
 * 字段引用按 ``field_name`` 解析(与后端一致,支持中文标识符)。
 */

type Tok = { t: string; v?: string };

const TOKEN_RE =
  /\s*(\d+\.?\d*|[A-Za-z_一-龥][A-Za-z0-9_一-龥]*|>=|<=|==|!=|[-+*/(),<>])/y;

function tokenize(src: string): Tok[] | null {
  const out: Tok[] = [];
  let i = 0;
  TOKEN_RE.lastIndex = 0;
  while (i < src.length) {
    if (/\s/.test(src[i])) { i++; continue; }
    TOKEN_RE.lastIndex = i;
    const m = TOKEN_RE.exec(src);
    if (!m) return null;
    const raw = m[1];
    i = TOKEN_RE.lastIndex;
    if (/^\d/.test(raw)) out.push({ t: "num", v: raw });
    else if (/^[A-Za-z_一-龥]/.test(raw)) out.push({ t: "id", v: raw });
    else out.push({ t: raw });
  }
  return out;
}

const FUNCS = new Set(["IF", "ROUND", "MIN", "MAX", "ABS"]);

/** 递归下降解析 + 即时求值。values 按 field_name 取数(非数值→0)。 */
function makeParser(toks: Tok[], values: Record<string, unknown>) {
  let p = 0;
  const peek = () => toks[p];
  const eat = (t?: string) => {
    const tok = toks[p];
    if (t && (!tok || tok.t !== t)) throw new Error("parse");
    p++;
    return tok;
  };
  const num = (x: unknown): number => {
    const n = typeof x === "number" ? x : parseFloat(String(x ?? ""));
    return Number.isFinite(n) ? n : 0;
  };

  function expr(): number { return comparison(); }

  function comparison(): number {
    let left = additive();
    const tok = peek();
    if (tok && [">", "<", ">=", "<=", "==", "!="].includes(tok.t)) {
      eat();
      const right = additive();
      switch (tok.t) {
        case ">": return left > right ? 1 : 0;
        case "<": return left < right ? 1 : 0;
        case ">=": return left >= right ? 1 : 0;
        case "<=": return left <= right ? 1 : 0;
        case "==": return left === right ? 1 : 0;
        case "!=": return left !== right ? 1 : 0;
      }
    }
    return left;
  }

  function additive(): number {
    let v = multiplicative();
    for (;;) {
      const tok = peek();
      if (tok?.t === "+") { eat(); v += multiplicative(); }
      else if (tok?.t === "-") { eat(); v -= multiplicative(); }
      else return v;
    }
  }

  function multiplicative(): number {
    let v = unary();
    for (;;) {
      const tok = peek();
      if (tok?.t === "*") { eat(); v *= unary(); }
      else if (tok?.t === "/") {
        eat();
        const d = unary();
        if (d === 0) throw new Error("div0");
        v /= d;
      } else return v;
    }
  }

  function unary(): number {
    if (peek()?.t === "-") { eat(); return -unary(); }
    return primary();
  }

  function primary(): number {
    const tok = peek();
    if (!tok) throw new Error("eof");
    if (tok.t === "num") { eat(); return parseFloat(tok.v!); }
    if (tok.t === "(") { eat("("); const v = expr(); eat(")"); return v; }
    if (tok.t === "id") {
      const name = tok.v!;
      eat();
      if (peek()?.t === "(") {
        // 函数调用
        const fn = name.toUpperCase();
        if (!FUNCS.has(fn)) throw new Error("unknown_fn");
        eat("(");
        const args: number[] = [];
        if (peek()?.t !== ")") {
          args.push(expr());
          while (peek()?.t === ",") { eat(","); args.push(expr()); }
        }
        eat(")");
        return applyFn(fn, args);
      }
      return num(values[name]);
    }
    throw new Error("unexpected");
  }

  function applyFn(fn: string, a: number[]): number {
    switch (fn) {
      case "IF": return a[0] ? a[1] : a[2];
      case "ROUND": {
        const f = Math.pow(10, a[1] ?? 0);
        return Math.round(a[0] * f) / f;
      }
      case "MIN": return Math.min(...a);
      case "MAX": return Math.max(...a);
      case "ABS": return Math.abs(a[0]);
      default: throw new Error("unknown_fn");
    }
  }

  return { run: () => { const v = expr(); if (p !== toks.length) throw new Error("trailing"); return v; } };
}

/** 求值 ``expression``;成功返回 number,任何失败返回 null(调用方回退已存值)。 */
export function evalFormulaPreview(
  expression: string,
  row: Record<string, unknown>,
): number | null {
  try {
    const toks = tokenize(expression);
    if (!toks || toks.length === 0) return null;
    const v = makeParser(toks, row).run();
    return Number.isFinite(v) ? v : null;
  } catch {
    return null;
  }
}
