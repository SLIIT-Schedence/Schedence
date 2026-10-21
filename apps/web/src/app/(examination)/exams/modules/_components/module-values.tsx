import { Badge } from "@/components/console/ui";
import type { ExamModuleDetails } from "@/lib/examination";
import { moduleValue, type ModuleColumn } from "@/lib/examination-module-catalog";

export function MissingValue() {
  return <Badge tone="accent">Not available</Badge>;
}

export function ModuleValue({ module, field }: { module: ExamModuleDetails; field: ModuleColumn }) {
  return moduleValue(module, field) ?? <MissingValue />;
}

export function RecordedText({ value }: { value: string | null | undefined }) {
  return value?.trim() ? value : <MissingValue />;
}

export function RecordedBoolean({ value }: { value: boolean | null | undefined }) {
  return typeof value === "boolean" ? (value ? "Yes" : "No") : <MissingValue />;
}

export function RecordedMinutes({ value }: { value: number | null | undefined }) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? `${value} min` : <MissingValue />;
}
