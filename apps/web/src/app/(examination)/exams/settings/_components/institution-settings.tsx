"use client";

import { useState } from "react";
import type { SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";
import { InstitutionSettingsEditor } from "./institution-settings-editor";

export function InstitutionSettings({ scheduling, seating }: { scheduling: SchedulingReview; seating: SeatingReview }) {
  const [review, setReview] = useState({ scheduling, seating });
  return <InstitutionSettingsEditor review={review} onSaved={(nextScheduling, nextSeating) => setReview({ scheduling: nextScheduling, seating: nextSeating })} />;
}
