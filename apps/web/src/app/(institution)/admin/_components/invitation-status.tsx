import { Badge } from "@/components/console/ui";
import type { ProvisioningStatus } from "@/lib/identity-records";

const STATUS: Record<ProvisioningStatus, { label: string; tone: "primary" | "accent" | "danger" | "neutral" }> = {
  COMPLETED: { label: "Sent", tone: "accent" },
  PENDING: { label: "In progress", tone: "primary" },
  COGNITO_CREATED: { label: "In progress", tone: "primary" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

/** An onboarding record's state in words: Sent, In progress, Failed or Cancelled. */
export function InvitationStatus({ status }: { status: ProvisioningStatus }) {
  const { label, tone } = STATUS[status] ?? { label: status, tone: "neutral" };
  return <Badge tone={tone}>{label}</Badge>;
}

export const canRetry = (status: ProvisioningStatus) => status === "PENDING" || status === "COGNITO_CREATED" || status === "FAILED";
export const canCancel = (status: ProvisioningStatus) => status === "PENDING" || status === "FAILED";
