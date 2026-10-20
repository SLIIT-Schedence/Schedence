import Link from "next/link";
import { Card, EmptyState } from "@/components/console/ui";

export default function NotFound() {
  return <Card><EmptyState title="Examination page not found" action={<Link href="/exams" className="rounded-lg text-sm font-semibold text-primary-strong">Back to overview</Link>}>Choose a section from the examination navigation.</EmptyState></Card>;
}
