import { Card, EmptyState } from "@/components/console/ui";
import { FIELD_LABELS, type FormQuestion } from "@/lib/form-template-contracts";
import { CONTROL } from "./styles";

const readable = (text: string) => text.charAt(0) + text.slice(1).toLowerCase();
export function FormPreview({ title, description, questions }: { title: string; description: string; questions: FormQuestion[] }) {
  return <div className="space-y-5">
    <Card><div className="p-5"><p className="text-xs font-semibold uppercase tracking-wider text-primary-strong">Form preview</p>
      <h2 className="mt-2 text-xl font-semibold text-ink">{title.trim() || "Untitled form"}</h2>
      {description && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{description}</p>}
      <p className="mt-3 text-[13px] text-muted">Preview only. Responses are not collected here.</p>
    </div></Card>
    {!questions.length && <Card><EmptyState icon="edit_note" title="No questions yet">Add a question in the editor to preview it here.</EmptyState></Card>}
    {questions.map((q, index) => <Card key={q.id}><fieldset disabled className="min-w-0 p-5" aria-label={q.label || `Question ${index + 1}`}>
      <legend className="sr-only">{q.label || `Question ${index + 1}`}</legend>
      <p className="text-[15px] font-semibold text-ink">{index + 1}. {q.label.trim() || "Untitled question"}{q.required && <span className="ml-1 text-danger" aria-label="required">*</span>}</p>
      {q.help_text && <p className="mt-1 whitespace-pre-wrap text-[13px] leading-5 text-muted">{q.help_text}</p>}
      <div className="mt-3"><PreviewInput question={q} /></div>
    </fieldset></Card>)}
  </div>;
}

function PreviewInput({ question: q }: { question: FormQuestion }) {
  if (q.field_type === "availability_matrix" && q.matrix) {
    return <div className="overflow-x-auto rounded-xl border border-line"><table className="w-full min-w-[420px] text-left text-sm">
      <thead className="bg-background text-xs text-muted"><tr><th scope="col" className="p-3">Day</th>{q.matrix.day_parts.map((part) => <th key={part} scope="col" className="p-3">{readable(part)}</th>)}</tr></thead>
      <tbody className="divide-y divide-line">{q.matrix.days.map((day) => <tr key={day}><th scope="row" className="p-3 font-medium">{readable(day)}</th>{q.matrix!.day_parts.map((part) =>
        <td key={part} className="p-2"><select className={CONTROL} aria-label={`${q.label}: ${readable(day)} ${readable(part)}`} defaultValue=""><option value="">Choose preference</option>{q.matrix!.values.map((value) => <option key={value}>{value}</option>)}</select></td>)}</tr>)}</tbody>
    </table></div>;
  }
  if (q.field_type === "module_preferences") return <input className={CONTROL} placeholder="Select module preferences" aria-label={q.label} />;
  if (q.field_type === "single_choice" || q.field_type === "multi_choice") return <div className="space-y-2">{q.choices.map((choice, index) => <label key={`${choice}:${index}`} className="flex items-center gap-2 text-sm text-ink">
    <input type={q.field_type === "single_choice" ? "radio" : "checkbox"} name={`preview-${q.id}`} className="accent-primary" />{choice}
  </label>)}</div>;
  if (q.field_type === "boolean") return <select className={CONTROL} aria-label={q.label} defaultValue=""><option value="">Choose an answer</option><option>Yes</option><option>No</option></select>;
  if (q.field_type === "long_text") return <textarea className={CONTROL} rows={3} aria-label={q.label} placeholder="Long answer" />;
  return <input className={CONTROL} type={q.field_type === "number" || q.field_type === "date" ? q.field_type : "text"}
    min={q.minimum ?? undefined} max={q.maximum ?? undefined} aria-label={q.label} placeholder={FIELD_LABELS[q.field_type]} />;
}
