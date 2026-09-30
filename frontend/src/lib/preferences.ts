"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, PageResult, Skill } from "./api";

export interface Preferences {
  skill: string;
  format: string;
  pages: number;
  style: string;
}
export function usePreferences() {
  return useQuery({
    queryKey: ["preferences"],
    queryFn: () => api<Preferences>("/me/preferences"),
  });
}
export function useAvailableSkills() {
  return useQuery({
    queryKey: ["skills", "options"],
    queryFn: async () => {
      const items: Skill[] = [];
      let cursor: string | null = null;
      do {
        const page: PageResult<Skill> = await api(
          `/skills?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
        );
        items.push(...page.items);
        cursor = page.nextCursor;
      } while (cursor);
      return { items, nextCursor: null };
    },
  });
}
export function usePreferredSkill(initial?: string) {
  const preferences = usePreferences();
  const skills = useAvailableSkills();
  const [override, setSkill] = useState<string | undefined>(initial);
  const candidate = override ?? preferences.data?.skill ?? "";
  const available = skills.data?.items.some((item) => item.id === candidate);
  const skill = override !== undefined || available ? candidate : "";
  const unavailable =
    override && skills.data && !available
      ? new Error("所选 Skill 已不可用，请重新选择。")
      : null;
  return {
    skill,
    setSkill,
    skills,
    pending:
      skills.isPending || (override === undefined && preferences.isPending),
    error:
      skills.error ||
      unavailable ||
      (override === undefined ? preferences.error : null),
  };
}
