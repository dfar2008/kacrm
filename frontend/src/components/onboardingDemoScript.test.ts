import {
  ACTS, INITIAL_STATE, advance, jumpToAct, type DemoState,
} from "./onboardingDemoScript";

// lengths[act][line]:解析后文本长度。测试给 user 行统一 3 个字,其余 0。
const lengths = ACTS.map((a) => a.lines.map((l) => (l.role === "user" ? 3 : 0)));

function run(state: DemoState, ticks: number): DemoState {
  for (let i = 0; i < ticks; i++) state = advance(state, lengths);
  return state;
}

test("script has 8 acts, each with a title and at least one line", () => {
  expect(ACTS.length).toBe(8);
  for (const act of ACTS) {
    expect(act.titleKey).toBeTruthy();
    expect(act.lines.length).toBeGreaterThan(0);
  }
});

test("user line types one char per tick", () => {
  // 第 2 幕首行是 user
  let s: DemoState = { act: 1, line: 0, chars: 0, hold: 0 };
  s = advance(s, lengths);
  expect(s).toEqual({ act: 1, line: 0, chars: 1, hold: 0 });
  s = advance(s, lengths);
  expect(s.chars).toBe(2);
});

test("first act opens with a user line (lead → account + opportunity)", () => {
  expect(ACTS[0].lines[0].role).toBe("user");
});

test("a non-user (agent) line shows whole, no typing, only holds", () => {
  // 第 1 幕第二行是 agent 回复:整条出现,只 hold 不打字
  const s = advance({ act: 0, line: 1, chars: 0, hold: 0 }, lengths);
  expect(s).toEqual({ act: 0, line: 1, chars: 0, hold: 1 });
});

test("after full text the line holds, then moves to next line in the act", () => {
  const holdTicks = ACTS[1].lines[0].holdTicks;
  let s = run({ act: 1, line: 0, chars: 0, hold: 0 }, 3); // 打完 3 个字
  expect(s).toEqual({ act: 1, line: 0, chars: 3, hold: 0 });
  s = run(s, holdTicks);                                  // hold 耗尽
  expect(s.hold).toBe(holdTicks);
  s = advance(s, lengths);                                // 进本幕下一行
  expect(s).toEqual({ act: 1, line: 1, chars: 0, hold: 0 });
});

test("finishing the last line of an act advances to the next act", () => {
  const act = 0;
  const lastLine = ACTS[act].lines.length - 1;
  const s: DemoState = {
    act, line: lastLine, chars: 0, hold: ACTS[act].lines[lastLine].holdTicks,
  };
  expect(advance(s, lengths)).toEqual({ act: 1, line: 0, chars: 0, hold: 0 });
});

test("finishing the last act loops back to INITIAL_STATE", () => {
  const act = ACTS.length - 1;
  const lastLine = ACTS[act].lines.length - 1;
  const s: DemoState = {
    act, line: lastLine, chars: 0, hold: ACTS[act].lines[lastLine].holdTicks,
  };
  expect(advance(s, lengths)).toEqual(INITIAL_STATE);
});

test("act 8 (last) is agent-initiated (no user/peer line)", () => {
  expect(ACTS[7].lines.every((l) => l.role === "agent")).toBe(true);
});

test("act 5 is the receipt-upload act: user line carries an attachment", () => {
  const upload = ACTS[4];
  expect(upload.lines[0].role).toBe("user");
  expect(upload.lines[0].attachKey).toBeTruthy();      // 气泡附件缩略
  const agentLines = upload.lines.filter((l) => l.role === "agent");
  expect(agentLines[agentLines.length - 1].card?.kind).toBe("fields"); // 识别结果卡
});

test("act 3 shows a searching beat before the ai-research card", () => {
  const agentLines = ACTS[2].lines.filter((l) => l.role === "agent");
  expect(agentLines.length).toBe(2);
  expect(agentLines[0].card).toBeUndefined();          // 进行态:纯气泡
  expect(agentLines[1].card?.kind).toBe("ai-research"); // 结果卡
});

test("jumpToAct resets line/chars/hold and clamps out-of-range", () => {
  expect(jumpToAct(3)).toEqual({ act: 3, line: 0, chars: 0, hold: 0 });
  expect(jumpToAct(-1)).toEqual(INITIAL_STATE);
  expect(jumpToAct(99).act).toBe(ACTS.length - 1);
});

test("out-of-range state resets (defensive)", () => {
  expect(advance({ act: 99, line: 0, chars: 0, hold: 0 }, lengths)).toEqual(INITIAL_STATE);
  expect(advance({ act: 0, line: 99, chars: 0, hold: 0 }, lengths)).toEqual(INITIAL_STATE);
});
