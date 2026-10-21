// Browser-safe Phase 1 Teaching Service contracts; authentication and API calls stay on the server.
export const LECTURER_PREFERENCES = "lecturer-teaching-preferences.v1";
export const CUSTOM_TYPES = ["text", "long_text", "number", "boolean", "date", "single_choice", "multi_choice"] as const;
export type CustomFieldType = (typeof CUSTOM_TYPES)[number];
export type FieldType = CustomFieldType | "availability_matrix" | "module_preferences";
export const FIELD_LABELS: Record<FieldType, string> = {
  text: "Short answer", long_text: "Paragraph", number: "Number", boolean: "Yes / No", date: "Date",
  single_choice: "Single choice", multi_choice: "Multiple choice", availability_matrix: "Day-part availability",
  module_preferences: "Module preferences",
};
export type FormQuestion = {
  id: string;
  kind: "LOCKED" | "SUGGESTED" | "CUSTOM";
  label: string;
  help_text: string;
  field_key: string | null;
  field_type: FieldType;
  required: boolean;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  matrix: { days: string[]; day_parts: string[]; values: string[] } | null;
  module_reference: "offering_id" | null;
};
export type FormPreset = { id: typeof LECTURER_PREFERENCES; schema_version: 1; title: string; description: string; questions: FormQuestion[] };
export type DraftFormTemplate = {
  id: string; institution_id: string; schema_version: 1; revision: number; status: "DRAFT";
  preset_id: typeof LECTURER_PREFERENCES | null; title: string; description: string; questions: FormQuestion[];
  created_by: string; updated_by: string; created_at: string; updated_at: string;
};
export type DraftFormPage = { items: DraftFormTemplate[]; total: number; page: number; page_size: number };
export type CreateDraft = { institution_id: string; title: string; description: string; preset_id?: typeof LECTURER_PREFERENCES };
export type SaveDraft = { institution_id: string; expected_revision: number; title: string; description: string; questions: FormQuestion[] };
export type QuestionDraft = { question: FormQuestion; choicesText: string; minimumText: string; maximumText: string };

export function questionDraft(question: FormQuestion): QuestionDraft {
  return { question, choicesText: question.choices.join("\n"), minimumText: question.minimum?.toString() ?? "", maximumText: question.maximum?.toString() ?? "" };
}

export function customQuestion(): QuestionDraft {
  return questionDraft({ id: `custom.${crypto.randomUUID()}`, kind: "CUSTOM", label: "", help_text: "", field_key: null,
    field_type: "text", required: false, choices: [], minimum: null, maximum: null, matrix: null, module_reference: null });
}

export function changeFieldType(draft: QuestionDraft, field_type: CustomFieldType): QuestionDraft {
  if (draft.question.kind === "LOCKED") return draft;
  const choice = field_type === "single_choice" || field_type === "multi_choice";
  return { ...draft, question: { ...draft.question, field_type, choices: [], minimum: null, maximum: null },
    choicesText: choice ? draft.choicesText || "Option 1\nOption 2" : "", minimumText: "", maximumText: "" };
}

export function moveQuestion(rows: QuestionDraft[], index: number, direction: -1 | 1): QuestionDraft[] {
  const target = index + direction;
  if (target < 0 || target >= rows.length) return rows;
  const next = [...rows];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

const lockedShape = (question: FormQuestion) => JSON.stringify(Object.fromEntries(
  Object.entries(question).filter(([key]) => key !== "label" && key !== "help_text").sort(([a], [b]) => a.localeCompare(b)),
));

export function validateDraft(title: string, description: string, drafts: QuestionDraft[], original: FormQuestion[]) {
  const errors: Record<string, string> = {};
  if (!title.trim() || title.trim().length > 200) errors.title = "Enter a form title of 1–200 characters.";
  if (description.length > 4000) errors.description = "Keep the description within 4,000 characters.";
  if (drafts.length > 100) errors.questions = "A form can have at most 100 questions.";
  const ids = new Set<string>();
  const questions = drafts.map((draft) => {
    const q = draft.question;
    if (ids.has(q.id)) errors.questions = "Each question must have a unique ID.";
    ids.add(q.id);
    if (!q.label.trim() || q.label.trim().length > 200) errors[q.id] = "Enter question wording of 1–200 characters.";
    if (q.help_text.length > 4000) errors[q.id] = "Keep question guidance within 4,000 characters.";
    if (q.kind === "LOCKED") return { ...q, label: q.label.trim() };
    const choices = q.field_type === "single_choice" || q.field_type === "multi_choice" ? draft.choicesText.split("\n").map((v) => v.trim()).filter(Boolean) : [];
    if ((q.field_type === "single_choice" || q.field_type === "multi_choice") &&
      (!choices.length || choices.length > 100 || choices.some((v) => v.length > 200) || new Set(choices).size !== choices.length)) {
      errors[q.id] = "Add 1–100 distinct choices, up to 200 characters each, one per line.";
    }
    const minimum = q.field_type === "number" && draft.minimumText.trim() ? Number(draft.minimumText) : null;
    const maximum = q.field_type === "number" && draft.maximumText.trim() ? Number(draft.maximumText) : null;
    if ((minimum !== null && !Number.isFinite(minimum)) || (maximum !== null && !Number.isFinite(maximum)) ||
      (minimum !== null && maximum !== null && minimum > maximum)) errors[q.id] = "Enter valid bounds with minimum at or below maximum.";
    return { ...q, label: q.label.trim(), choices, minimum, maximum };
  });
  for (const locked of original.filter((q) => q.kind === "LOCKED")) {
    const current = questions.find((q) => q.id === locked.id);
    if (!current || lockedShape(current) !== lockedShape(locked)) errors.questions = "Locked questions can change wording only. Reload the saved draft to restore their format.";
  }
  return { questions, errors, valid: Object.keys(errors).length === 0 };
}

export const formsHref = (institution: string, id?: string) => `/academic/forms${id ? `/${encodeURIComponent(id)}` : ""}?institution=${encodeURIComponent(institution)}`;

export function formErrorMessage(error: { status: number; code: string; message: string }): string {
  if (error.status === 409) return "Someone saved a newer version of this form. Your edits are kept. Reload the latest draft before saving again.";
  if (error.status === 403) return "You do not have permission to manage forms for this institution.";
  if (error.status === 0) return "The Teaching Service could not be reached. Your edits are kept; try again.";
  if (error.status >= 500) return "Draft forms are temporarily unavailable. Your edits are kept; try again later.";
  if (error.status === 422 && error.code === "VALIDATION_ERROR") return "Check the question wording, choices and numeric bounds, then save again.";
  return error.message;
}
