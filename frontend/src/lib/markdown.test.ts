import { renderMarkdown } from "./markdown";

// Regression: the agent often replies with a numbered option list ("回我一个
// 数字就行"). Ordered lists MUST render with their numbers, not collapse into
// bullets (which left SPA users with nothing to pick while feishu showed 1-5).

test("ordered list → <ol> (numbers), never <ul>", () => {
  const html = renderMarkdown("1. 清杂草\n2. AI 教练\n3. 建规则");
  expect(html).toContain("<ol>");
  expect(html).toContain("<li>清杂草</li>");
  expect(html).toContain("<li>AI 教练</li>");
  expect(html).not.toContain("<ul>"); // must not become bullets
});

test("unordered list stays <ul>", () => {
  const html = renderMarkdown("- 甲\n- 乙");
  expect(html).toContain("<ul>");
  expect(html).toContain("<li>甲</li>");
  expect(html).not.toContain("<ol>");
});

test("ordered items keep no stray class attribute in output", () => {
  const html = renderMarkdown("1. a\n2. b");
  expect(html).not.toContain("md-ol");
  expect(html).not.toContain("md-ul");
});
