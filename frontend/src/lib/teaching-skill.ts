export interface TeachingSkillFields {
  subject: string;
  grade: string;
  topic: string;
  goal: string;
  result: string;
  notes: string;
}

export const emptyTeachingSkill: TeachingSkillFields = {
  subject: "",
  grade: "高一",
  topic: "",
  goal: "",
  result: "教学方案",
  notes: "",
};

const fieldLabels: Record<keyof TeachingSkillFields, string> = {
  subject: "学科",
  grade: "适用年级",
  topic: "教学主题",
  goal: "希望学生学会什么",
  result: "希望得到什么",
  notes: "其他教学要求",
};

export function buildTeachingSkill(fields: TeachingSkillFields) {
  const values = Object.fromEntries(
    (Object.keys(fieldLabels) as (keyof TeachingSkillFields)[]).map((key) => [
      key,
      fields[key].replace(/\s+/g, " ").trim(),
    ]),
  ) as unknown as TeachingSkillFields;
  return [
    "请担任教师的备课助手。根据教师本次提供的具体问题或资料，完成下面的教学任务；不要编造教材内容或学生情况。",
    ...Object.entries(fieldLabels).map(([key, label]) =>
      `【${label}】${values[key as keyof TeachingSkillFields] || "未填写"}`,
    ),
    "请先确认本次问题是否属于上述学科、年级和主题。若信息不足，先向教师询问；若适用，再围绕教学目标给出可执行的课堂活动、例题或评价建议。",
    "输出应适合教师直接阅读和修改，标出需要教师核对的事实与课堂安排；不要把生成内容当作对学生的最终评价。",
  ].join("\n");
}

export function parseTeachingSkill(prompt: string): TeachingSkillFields | null {
  const fields = { ...emptyTeachingSkill };
  if (!prompt.startsWith("请担任教师的备课助手。")) return null;
  for (const [key, label] of Object.entries(fieldLabels)) {
    const line = prompt.split("\n").find((part) => part.startsWith(`【${label}】`));
    if (!line) return null;
    fields[key as keyof TeachingSkillFields] = line.slice(label.length + 2).replace(/^未填写$/, "");
  }
  return fields;
}

export function teachingSkillExamples(fields: TeachingSkillFields) {
  const topic = fields.topic || "本课主题";
  const goal = fields.goal || "本课目标";
  const result = fields.result || "教学建议";
  return [
    { input: `请围绕${topic}设计一节课的导入活动。`, expectedOutput: `给出贴近学生经验的导入活动、提问方式，并说明如何引向${goal}。` },
    { input: `学生对${topic}理解不清，怎样讲解更容易懂？`, expectedOutput: `用准确的学科语言和一个具体例子解释重点，并指出教师需要核对的内容。` },
    { input: `请为${topic}设计一个小组探究活动。`, expectedOutput: `说明活动步骤、学生任务、教师引导问题和课堂反馈方式，围绕${goal}展开。` },
    { input: `如何判断学生是否掌握了${topic}？`, expectedOutput: `给出课堂提问或练习示例、观察要点和评价建议，不直接替教师给学生打分。` },
    { input: `请整理${topic}的课后练习和改进建议。`, expectedOutput: `给出分层练习与反馈思路，最终内容适合整理成${result}，提醒教师按班级情况调整。` },
  ];
}
