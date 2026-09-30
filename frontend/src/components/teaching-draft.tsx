"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
interface UnsavedDraft { content: string; revision: number }
interface TeachingDraft {
  prompt: string;
  skill: string;
  skillChosen?: boolean;
  file: File | null;
}
const empty: TeachingDraft = { prompt: "", skill: "", file: null };
const STORAGE_KEY = "teaching-unsaved-drafts-v1";
const Context = createContext<{
  draft: TeachingDraft;
  setDraft: (draft: TeachingDraft) => void;
  edits: Map<string, UnsavedDraft>;
  setEdit: (key: string, value: UnsavedDraft) => void;
  deleteEdit: (key: string) => void;
} | null>(null);
export function TeachingDraftProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [draft, setDraft] = useState<TeachingDraft>(empty);
  const [edits] = useState(() => {
    const current = new Map<string, UnsavedDraft>();
    if (typeof window === "undefined") return current;
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(saved)) {
        for (const row of saved) {
          if (
            Array.isArray(row) &&
            typeof row[0] === "string" &&
            typeof row[1]?.content === "string" &&
            Number.isInteger(row[1]?.revision)
          ) current.set(row[0], row[1]);
        }
      }
    } catch {
      // Storage can be unavailable in private browsing; in-memory recovery still works.
    }
    return current;
  });
  const persist = useCallback(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...edits]));
    } catch {
      // A full or disabled session store must not block editing.
    }
  }, [edits]);
  const setEdit = useCallback((key: string, value: UnsavedDraft) => {
    edits.set(key, value);
    persist();
  }, [edits, persist]);
  const deleteEdit = useCallback((key: string) => {
    edits.delete(key);
    persist();
  }, [edits, persist]);
  useEffect(() => {
    const protectEdits = (event: BeforeUnloadEvent) => {
      if (!edits.size) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectEdits);
    return () => window.removeEventListener("beforeunload", protectEdits);
  }, [edits]);
  return (
    <Context.Provider value={{ draft, setDraft, edits, setEdit, deleteEdit }}>{children}</Context.Provider>
  );
}
export function useTeachingDraft() {
  const context = useContext(Context);
  if (!context) throw new Error("TeachingDraftProvider is required");
  return context;
}
