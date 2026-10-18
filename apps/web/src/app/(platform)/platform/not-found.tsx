import Link from "next/link";
import { Card, EmptyState } from "@/components/console/ui";

export default function PlatformNotFound() {
  return (
    <Card>
      <EmptyState
        icon="error"
        title="Not found"
        action={
          <Link
            href="/platform/institutions"
            className="inline-flex h-10 items-center rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            Go to institutions
          </Link>
        }
      >
        This institution doesn&apos;t exist, or the link is incomplete.
      </EmptyState>
    </Card>
  );
}
