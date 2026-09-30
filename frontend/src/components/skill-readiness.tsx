"use client";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, json, Skill } from "@/lib/api";
import { QueryState } from "./query-state";
import { emptyTeachingSkill, parseTeachingSkill, teachingSkillExamples } from "@/lib/teaching-skill";
interface Readiness {
  riskBoundary: string;
  grades: string[];
  tests: { input: string; expectedOutput: string }[];
}
function ReadinessForm({
  skill,
  initial,
  onClose,
}: {
  skill: Skill;
  initial: Readiness;
  onClose: () => void;
}) {
  const [riskBoundary, setRiskBoundary] = useState(initial.riskBoundary);
  const [grades, setGrades] = useState(() => {
    const suggested = parseTeachingSkill(skill.prompt || "")?.grade;
    return initial.grades.length ? initial.grades : suggested && ["高一", "高二", "高三"].includes(suggested) ? [suggested] : [];
  });
  const [tests, setTests] = useState(
    initial.tests.length
      ? initial.tests
      : Array.from({ length: 5 }, () => ({ input: "", expectedOutput: "" })),
  );
  const save = useMutation({
    mutationFn: () =>
      api(
        `/skills/${skill.id}/readiness`,
        json("PUT", { riskBoundary, grades, tests }),
      ),
    onSuccess: onClose,
  });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p>
        想把这个教学 Skill 分享给全校？请选择年级，写下不适用的情况，再给出五个课堂问题和你希望看到的回答。保存后即可提交学校审核。
      </p>
      <label>
        哪些情况需要你亲自核对，或不适合让助手直接回答？
        <textarea
          required
          maxLength={3000}
          value={riskBoundary}
          onChange={(e) => setRiskBoundary(e.target.value)}
          placeholder="例如：不替教师给学生打分；涉及教材原文和考试政策时先核对资料。"
        />
      </label>
      <fieldset className="grade-options">
        <legend>适合哪些年级？</legend>
        {["高一", "高二", "高三"].map((g) => (
          <label key={g}>
            <input
              type="checkbox"
              checked={grades.includes(g)}
              onChange={(e) =>
                setGrades(
                  e.target.checked
                    ? [...grades, g]
                    : grades.filter((x) => x !== g),
                )
              }
            />
            {g}
          </label>
        ))}
      </fieldset>
      <div className="skill-example-helper">
        <p>可以先生成五个课堂试用问题，再按自己的教学实际修改每一条。</p>
        <button type="button" className="soft-button"
          disabled={tests.some((item) => item.input.trim() || item.expectedOutput.trim())}
          onClick={() => {
          const fields = parseTeachingSkill(skill.prompt || "") || {
            ...emptyTeachingSkill, topic: skill.name, goal: skill.description,
          };
          setTests(teachingSkillExamples(fields));
        }}>生成课堂问题示例</button>
      </div>
      {tests.map((t, i) => (
        <fieldset className="sample-case" key={i}>
          <legend>试用问题 {i + 1}</legend>
          <label>
            老师会怎样提问？
            <textarea
              required
              maxLength={5000}
              value={t.input}
              placeholder="例如：帮我设计一节关于函数单调性的探究课。"
              onChange={(e) =>
                setTests(
                  tests.map((x, j) =>
                    j === i ? { ...x, input: e.target.value } : x,
                  ),
                )
              }
            />
          </label>
          <label>
            你希望助手回答到什么程度？
            <textarea
              required
              maxLength={10000}
              value={t.expectedOutput}
              placeholder="例如：列出教学目标、课堂活动、随堂练习和评价方法。"
              onChange={(e) =>
                setTests(
                  tests.map((x, j) =>
                    j === i ? { ...x, expectedOutput: e.target.value } : x,
                  ),
                )
              }
            />
          </label>
        </fieldset>
      ))}
      {save.error && <p role="alert">{save.error.message}</p>}
      <button
        className="start-button"
        disabled={save.isPending || grades.length === 0}
      >
        保存试用说明
      </button>
    </form>
  );
}
export function SkillReadiness({
  skill,
  onClose,
}: {
  skill: Skill;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const data = useQuery({
    queryKey: ["readiness", skill.id],
    queryFn: () => api<Readiness>(`/skills/${skill.id}/readiness`),
    staleTime: 0,
  });
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog ref={dialog} aria-labelledby="readinessTitle" onCancel={onClose}>
      <div className="section-heading">
        <h2 id="readinessTitle">{skill.name} · 分享前检查</h2>
        <button
          className="soft-button"
          type="button"
          onClick={onClose}
          aria-label="关闭发布资料"
        >
          ×
        </button>
      </div>
      <QueryState
        pending={data.isPending}
        error={data.error}
        retry={() => void data.refetch()}
      />
      {data.data && (
        <ReadinessForm skill={skill} initial={data.data} onClose={onClose} />
      )}
    </dialog>
  );
}
