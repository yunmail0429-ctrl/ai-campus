import { expect, test } from "@playwright/test";
import { answerDraft } from "../src/lib/draft-content";
import { buildTeachingSkill, parseTeachingSkill, teachingSkillExamples } from "../src/lib/teaching-skill";

test("teacher fields generate a reusable template and five distinct classroom examples", () => {
  const fields = { subject: "语文", grade: "高一", topic: "议论文论证", goal: "识别事实论据",
    result: "课堂练习", notes: "兼顾基础薄弱学生" };
  const prompt = buildTeachingSkill(fields);
  expect(parseTeachingSkill(prompt)).toEqual(fields);
  expect(prompt).toContain("不要编造教材内容");
  const examples = teachingSkillExamples(fields);
  expect(examples).toHaveLength(5);
  expect(new Set(examples.map((example) => example.input)).size).toBe(5);
  expect(examples.every((example) => example.expectedOutput.trim())).toBe(true);
});

test("draft preserves citation numbering and locations without private payloads", () => {
  const result = answerDraft("教学回答 [3]", [
    {
      number: 3,
      title: "教材",
      location: "第 12 页",
      excerpt: "PRIVATE_EXCERPT",
      documentId: "PRIVATE_ID",
    },
    {
      number: 7,
      title: "PRIVATE_OLD_TITLE",
      location: "PRIVATE_OLD_LOCATION",
      unavailable: true,
    },
  ]);
  expect(result).toContain("教学回答 [3]");
  expect(result).toContain("\\[3\\] 教材 · 第 12 页");
  expect(result).toContain("\\[7\\] 来源已不可用");
  expect(result).not.toContain("PRIVATE");
  expect(answerDraft("普通回答")).toBe("普通回答");
  expect(
    answerDraft("正文", [
      { title: "标题\n# 伪标题 [链接](https://example.com)" },
    ]),
  ).toContain("标题 \\# 伪标题 \\[链接\\]");
});
