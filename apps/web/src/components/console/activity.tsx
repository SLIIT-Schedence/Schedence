import Link from "next/link";
import { describeEvent } from "@/lib/audit-events";
import type { AuditEntry, Institution } from "@/lib/platform";
import { formatDateTime, ToneIcon } from "./ui";

/** Names the audit log can't carry itself: the viewer's own ID, institutions, and known people. */
export type AuditLookups = {
  me: string;
  institutions: Map<string, Pick<Institution, "id" | "name" | "code">>;
  people?: Map<string, string>;
};

export function actorLabel(entry: AuditEntry, me: string): string {
  if (entry.actorType === "OPERATOR") {
    const operator = typeof entry.metadata.operator === "string" ? entry.metadata.operator : undefined;
    return operator ? `Operator (${operator})` : "Operator command";
  }
  if (entry.actorType === "SYSTEM" || !entry.actorUserId) return "Schedence (automatic)";
  return entry.actorUserId === me ? "You" : "Another system admin";
}

/** A compact feed of audit entries, newest first. */
export function ActivityFeed({
  entries,
  lookups,
  showInstitution = true,
  institutionHref,
}: {
  entries: AuditEntry[];
  lookups: AuditLookups;
  showInstitution?: boolean;
  /** Where an institution's name links to; plain text when omitted. */
  institutionHref?: (institutionId: string) => string;
}) {
  return (
    <ol className="divide-y divide-line">
      {entries.map((entry) => {
        const event = describeEvent(entry);
        const institution = entry.institutionId ? lookups.institutions.get(entry.institutionId) : undefined;
        const person = entry.targetUserId ? lookups.people?.get(entry.targetUserId) : undefined;
        return (
          <li key={entry.id} className="flex gap-3 px-5 py-3.5">
            <ToneIcon icon={event.icon} tone={event.tone} size="sm" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-5 text-ink">
                {event.label}
                {person && <span className="font-normal text-muted"> · {person}</span>}
              </p>
              {event.detail && <p className="mt-0.5 break-words text-[13px] leading-5 text-muted">{event.detail}</p>}
              <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-xs leading-5 text-muted">
                <span>{actorLabel(entry, lookups.me)}</span>
                {showInstitution && institution && (
                  <>
                    <span aria-hidden="true">·</span>
                    {institutionHref ? (
                      <Link
                        href={institutionHref(institution.id)}
                        className="font-medium text-primary-strong underline-offset-2 hover:underline"
                      >
                        {institution.name}
                      </Link>
                    ) : (
                      <span>{institution.name}</span>
                    )}
                  </>
                )}
                <span aria-hidden="true">·</span>
                <time dateTime={entry.occurredAt}>{formatDateTime(entry.occurredAt)}</time>
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
