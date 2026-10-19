import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDate, LoadError, PageSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getPosition, type HoursPolicy } from "@/lib/academic";
import { isUuid } from "@/lib/identity-records";
import {
  ACADEMIC_SERVICE,
  CodeTag,
  combinationRule,
  Fact,
  hours,
  period,
  PositionStatus,
  UNIT_TYPES,
} from "../../_components/academic-ui";

export const metadata: Metadata = { title: "Position" };

export default function PositionPage({ params }: PageProps<"/academic/positions/[positionId]">) {
  return (
    <div className="space-y-6">
      <Link
        href="/academic/positions"
        className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-muted transition-colors hover:text-primary-strong"
      >
        <Icon name="arrow_back" size={18} />
        All positions
      </Link>
      <Suspense fallback={<PageSkeleton />}>
        <PositionDetail params={params} />
      </Suspense>
    </div>
  );
}

async function PositionDetail({ params }: { params: PageProps<"/academic/positions/[positionId]">["params"] }) {
  const { positionId } = await params;
  if (!isUuid(positionId)) notFound();

  const result = await getPosition(positionId);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return (
      <Card>
        <LoadError error={result.error} what="this position" retryHref={`/academic/positions/${positionId}`} service={ACADEMIC_SERVICE} />
      </Card>
    );
  }

  const position = result.data;
  const history = position.history.toSorted((a, b) => b.version - a.version);

  return (
    <div className="space-y-6">
      <div className="min-w-0">
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded sm:text-[2rem]">
          {position.name}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <CodeTag>{position.code}</CodeTag>
          <PositionStatus position={position} />
        </div>
        {position.description && <p className="mt-3 max-w-2xl text-[15px] leading-6 text-muted">{position.description}</p>}
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Fact label="Hours a week now" value={position.hoursPerWeek === null ? "None in force" : hours(position.hoursPerWeek)}>
          {position.policy ? `Policy version ${position.policy.version}` : "Timetables can't count it yet"}
        </Fact>
        <Fact
          label="With other appointments"
          value={position.policy ? combinationRule(position.policy.combinationRule, position.policy.capHoursPerWeek) : "—"}
        />
        <Fact label="Held per" value={position.unitType ? UNIT_TYPES[position.unitType] : "No unit"}>
          {position.exclusivePerUnit ? "One holder at a time" : "Several holders allowed"}
        </Fact>
        <Fact label="Defined" value={formatDate(position.createdAt)}>
          Updated {formatDate(position.updatedAt)}
        </Fact>
      </dl>

      <Card title="Hours policy history" description="Each change to the hours adds a version and closes the previous one, so past timetables stay explainable.">
        {history.length === 0 ? (
          <EmptyState icon="schedule" title="No hours policy yet">
            Your Institution Admin sets the weekly hours this position takes.
          </EmptyState>
        ) : (
          <ol className="divide-y divide-line">
            {history.map((policy) => (
              <PolicyItem key={policy.id} policy={policy} current={policy.id === position.policy?.id} />
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function PolicyItem({ policy, current }: { policy: HoursPolicy; current: boolean }) {
  return (
    <li className="flex gap-4 px-5 py-4">
      <span
        className={`grid size-10 shrink-0 place-items-center rounded-xl font-mono text-[13px] font-semibold ${
          current ? "bg-primary text-white" : "bg-secondary-soft text-secondary-ink"
        }`}
      >
        v{policy.version}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[15px] font-semibold text-ink">{hours(policy.hoursPerWeek)} a week</p>
          {current && <Badge tone="accent">In force</Badge>}
        </div>
        <p className="mt-0.5 text-[13px] text-muted">
          {period(policy.effectiveFrom, policy.effectiveUntil)} · {combinationRule(policy.combinationRule, policy.capHoursPerWeek)}
        </p>
        {policy.reference && (
          <p className="mt-1.5 flex items-center gap-1.5 text-[13px] text-ink">
            <Icon name="fact_check" size={16} className="text-secondary" />
            {policy.reference}
          </p>
        )}
        {policy.notes && <p className="mt-1.5 text-[13px] leading-5 text-muted">{policy.notes}</p>}
      </div>
    </li>
  );
}
