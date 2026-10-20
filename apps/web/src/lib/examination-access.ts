import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { type Account, getAccount } from "@/lib/identity";

export const EXAMINATION_ADMIN = "EXAMINATION_ADMIN";

export function isExaminationAdmin(account: Account): boolean {
  return account.accountStatus === "ACTIVE" && account.roles.some((role) =>
    role.roleCode === EXAMINATION_ADMIN &&
    account.institutions.find((institution) => institution.id === role.institutionId)?.status !== "DISABLED",
  );
}

/** Check at the data boundary too: layouts do not prevent child routes from executing. */
export const requireExaminationAdmin = cache(async (): Promise<Account> => {
  const result = await getAccount();
  if (result.status !== "ok" || !isExaminationAdmin(result.account)) redirect("/");
  return result.account;
});
