# Reviewed Teaching–Examination module mappings

Read-only review, 2026-10-21. Existing Neon `neondb`, canonical institution
`84651d6d-0c8a-470a-a798-5220666fd367`. No database, service configuration,
source names/codes, permissions or historical records were changed.

**Module-identity gate: PASS for the 91 previously inspected local Teaching
definitions. Overall migration gate: not yet passed.** The result is 88 exact
Exam matches, 2 reviewed capitalization-only equivalents and 1 verified
Academic-only module. This does not approve future modules or all existing
cross-catalog differences automatically.

## Exact existing ID crosswalk

Examination's primary key is the exact string `module_code`; there is no Exam
module UUID to invent. Academic UUIDs below are existing source-record IDs,
not newly created or official offering identifiers.

| Code | Existing Academic UUID | Existing Exam primary key | Year / semester | Verified credits | Program(s) | Decision |
| --- | --- | --- | --- | ---: | --- | --- |
| `IT3060` | `9d1bf5bd-dd24-4e16-95f4-91988437f8f7` | **None** | 3 / 1 | 3 | DS, IT | Valid Academic-only `Human Computer Interaction`; reuse this ID without creating or guessing an Exam mapping. |
| `IE3030` | `358eb637-c63d-40fb-83a6-958565563c46` | `IE3030` | 3 / 1 | 4 | CSNE | `Wide area Networks` and `Wide Area Networks` differ only in capitalization. Exact code, period, credits and program match. |
| `IT3061` | `ba735af7-648e-4951-94ac-fa68ed69008d` | `IT3061` | 3 / 1 | 4 | DS | `Massive Data Processing and cloud Computing` and `Massive Data Processing and Cloud Computing` differ only in capitalization. Exact code, period, credits and program match. |
| `IT2100` | `956bed92-6cc5-4b16-9e22-58c1a92b66ac` | `IT2100` | 2 / 2 | 3 | CSNE | Unique complete match for `Probability and Statistics`. Select this Academic ID explicitly; do not merge the duplicate-code records. |

Existing program IDs supporting the matched identities:

| Program | Existing Academic specialization UUID |
| --- | --- |
| CSNE — Computer Systems & Network Engineering | `0e80c90f-1683-48be-bb59-abfb8996cc91` |
| DS — Data Science | `0b864111-a0d4-4799-a517-4d130c8fa69b` |
| IT — Information Technology | `0de43ffc-648a-4ae7-bf05-17758fc320ba` |

## Evidence and why IT2100 is resolved

All five inspected Academic records (including the held seminar) reference the
same UNIVERSITY_SOURCED module workbook:

- File: `Schedence_Modules_Department_Aligned_Instructors_Unchanged.xlsx`.
- Source UUID: `39893d91-b87d-450b-80bb-e9c5392503df`.
- SHA-256: `298297c521e8bb4fe04d82febd13a81dc0750faac401836ffb122f7a1a6fda78`.
- Worksheet: `Modules_183`.
- Original rows: IE3030 **40**, Probability and Statistics **100**, IT3060 **111**,
  IT3061 **112**, seminar **185**.

The local original workbook's checksum matches the stored source checksum.
Read-only workbook inspection independently confirmed `Module_Code`,
`Module_Name`, `Module_Credits` and `Specialization` at each exact source row.
Credits are available in `academic.modules.source_values.Module_Credits` even
though `academic.modules` has no normalized credit column. No contact-hour to
credit conversion or other inference was used.

| IT2100 evidence | Probability and Statistics | Seminar |
| --- | --- | --- |
| Existing Academic UUID | `956bed92-6cc5-4b16-9e22-58c1a92b66ac` | `45ca97cb-bf9b-4b01-8ec5-77808a8aeed9` |
| Exact name | Probability and Statistics | Employability Skills Development - Seminar (Non-GPA) |
| Year / semester / degree type | 2 / 2 / Both | 2 / 2 / Both |
| Source credits | **3** | **1** |
| Linked program(s) | **CSNE** | CYBER, DS, IM, ISE, IT, SE |
| Weekly source contact minutes | Lecture 120; tutorial 60 | Workshop 60 |
| Matches Exam `IT2100` name, credits and program | **Yes: exact name, 3 credits, CSNE** | **No** |

Exam's actual catalog row is `IT2100`, `Probability and Statistics`, Year 2 /
Semester 2, **3 credits**, **CSNE**, written final examination, 90 minutes.
Only the first Academic record matches all those academic identity fields.
The other record contradicts the name, credits and programs; code alone was
not used to choose a match.

Exam specialization, registration and period-membership tables use FK references
to `examinations.seed_modules(module_code)` with ON DELETE RESTRICT. Currently,
IT2100 has a CSNE specialization link but **zero registrations, period memberships
or saved session references**. No scheduled IT2100 session is invented as evidence.
The catalog identity is sufficient and uniquely corroborated by the original
Academic workbook; actual delivery of an IT2100 examination is not asserted.

For IT3061, additional existing Exam references corroborate its identity:
19 registrations, period `PP1-2026-S1-FINAL`, and one **APPROVED** saved session
using the Exam spelling, Year 3 / Semester 1. APPROVED is not PUBLISHED. IE3030
has no current registrations/period/session references; its matching catalog,
program, credits and original source row are the evidence used.

IT3060 has one canonical Academic row with 3 source credits, DS/IT links,
120 lecture + 120 lab minutes and 7 teaching-team links. It has no Exam row.
Its Academic-only status is valid under the user's explicit rule; no claim of
Exam eligibility or equivalence to a differently coded Exam module is made.

## Reuse contract and unresolved cases

The machine-readable local crosswalk is
`database/teaching_shared/reviewed_module_mappings.v1.json`. It pins the existing
IDs, institution, exact codes/names, credits, programs, source ID/row and checksum.
It changes no database records. Teaching may reuse these existing IDs when
preparing the next migration plan; no Academic master inserts are required.

- Apply these four decisions only to the recorded identities. Do not enable
  generic case-insensitive or fuzzy matching for other module names/codes.
- Resolve IT2100 by canonical institution **and Academic UUID**, then its reviewed
  Exam string key. Never join the unfiltered Academic catalog to Exam on code
  alone, because that would also attach the seminar to Probability and Statistics.
- Keep the seminar ID `45ca97cb-bf9b-4b01-8ec5-77808a8aeed9` unchanged and **unmapped
  to Exam**. Its correct official code, if any, still needs curriculum-owner
  confirmation. This unresolved source-catalog issue does not block reuse of
  the uniquely verified Statistics ID when explicit-ID selection is enforced.
- The remaining `IT3080`, `IT4100`, `IE4080` discrepancies are outside the current
  91-definition scope and are not resolved by these decisions. The 28 Exam-only
  records stay Exam-owned; do not import or normalize them automatically.
- Preserve all stored source names and Exam codes. An equivalent-name decision
  is a local crosswalk, not an UPDATE or merge.

The earlier shared-view SQL is still a review draft: its Academic-first module
contract must be revised to consume these explicit decisions before any future
approved deployment. No SQL migration was regenerated or executed in this review.

## Migration safety status

**The module-mapping blocker for the current 91 Teaching definitions is cleared.**
The full 335-row migration is **not** declared safe or executed by this review.
It still needs fresh verified backups, reviewed missing-table deployment and
role/grant plans, a fresh full deduplication/FK dry-run, and exclusion/handling
of the 99 unapproved experimental planning groups. No source payload, historical
tenant ID or immutable snapshot is silently rewritten to satisfy those gates.

This turn's changes are local evidence, the reviewed mapping artifact, supporting
read-only tooling, tests and documentation. Database inserts/updates/deletes,
schema/permission/configuration changes, solver runs, commits and pushes: **zero**.

Private evidence (ignored by Git):

- `scripts/neon-review/module-mapping-evidence-20261021-v1.json`: fresh scoped
  Neon module data and aggregate references.
- `scripts/neon-review/module-mapping-evidence-20261021-v2.json`: same evidence
  plus independently verified original workbook row fields.

Focused validation covers wrong tenant/ID, changed credits/program/period,
non-capitalization name differences, missing source verification, unexpected
new Exam references, duplicate evidence identities and unreviewed modules.
