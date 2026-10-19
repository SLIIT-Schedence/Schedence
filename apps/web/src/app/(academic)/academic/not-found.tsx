import Link from "next/link";
import { Card, EmptyState } from "@/components/console/ui";

export default function AcademicNotFound() {
  return (
    <Card>
      <EmptyState
        icon="error"
        title="Not found"
        action={
          <Link
            href="/academic"
            className="inline-flex h-10 items-center rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            Go to the overview
          </Link>
        }
      >
        This record doesn&apos;t exist in your institution, or the link is incomplete.
      </EmptyState>
    </Card>
  );
}
