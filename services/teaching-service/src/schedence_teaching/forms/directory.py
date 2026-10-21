"""Read-only Academic owner adapter; appointment titles and email never identify logins."""

from collections import Counter

from sqlalchemy import text

from schedence_teaching.persistence.database_read import readonly_owner

from .sending_contracts import EligibleLecturer


class AcademicLecturerDirectory:
    def __init__(self, engine, target):
        self.engine, self.target = engine, target

    async def eligible(self, institution):
        # Reuse the existing restricted-reader target, role and tenant checks.
        async with readonly_owner(self.engine, "academic", institution, self.target) as (connection, _):
            staff = (await connection.execute(text("""SELECT id, staff_number, full_name,
                identity_user_id, employment_status FROM academic.staff
                WHERE institution_id=:tenant ORDER BY id"""), {"tenant": institution})).mappings().all()
            profiles = (await connection.execute(text("""SELECT staff_number, department_id
                FROM academic.staff_teaching_profiles WHERE institution_id=:tenant
                AND staff_type IN ('ACADEMIC','INSTRUCTOR')"""), {"tenant": institution})).mappings().all()
            names = (await connection.execute(text("""SELECT id, name FROM academic.departments
                WHERE institution_id=:tenant"""), {"tenant": institution})).mappings().all()
        return self.resolve(staff, profiles, names)

    @staticmethod
    def resolve(staff, profiles, names=()):
        department_names = {row["id"]: row["name"] for row in names}
        departments = {}
        for profile in profiles:
            departments.setdefault(profile["staff_number"], set())
            if profile["department_id"] is not None:
                departments[profile["staff_number"]].add(profile["department_id"])
        # Count every linked staff record, including inactive/non-teaching records.
        links = Counter(row["identity_user_id"] for row in staff if row["identity_user_id"] is not None)
        result = []
        for row in staff:
            if row["employment_status"] != "ACTIVE" or row["staff_number"] not in departments:
                continue
            user = row["identity_user_id"]
            status = "NO_LINK" if user is None else "AMBIGUOUS_LINK" if user.int == 0 or links[user] != 1 else "READY"
            result.append(EligibleLecturer(staff_id=row["id"], staff_number=row["staff_number"],
                full_name=row["full_name"], identity_user_id=user, link_status=status,
                departments=tuple(dict(id=identifier, name=department_names[identifier])
                    for identifier in sorted(departments[row["staff_number"]], key=str) if identifier in department_names),
                department_ids=tuple(sorted(departments[row["staff_number"]], key=str))))
        return tuple(result)
