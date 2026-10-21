import { redirect } from "next/navigation";

// The duty-allocation overview has its own address so that its sidebar item is not marked active on every
// duty page (the console marks an item active for all addresses below it).
export default function DutyAllocationHome() {
  redirect("/exams/duties/overview");
}
