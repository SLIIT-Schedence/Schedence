import { Suspense } from "react";
import { Wordmark } from "@/components/wordmark";
import { UserMenu } from "./user-menu";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Wordmark />
          <Suspense fallback={<div className="h-9 w-36 animate-pulse rounded-lg bg-background" />}>
            <UserMenu />
          </Suspense>
        </div>
      </header>
      <main className="flex-1">{children}</main>
    </div>
  );
}
