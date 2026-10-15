# Campus data collection pack: Step 12A.1

Start with the [university request checklist](university-checklist.md). The
[field-level gap report](gap-report.csv) distinguishes confirmed/reusable values,
missing data, ambiguity, policy confirmation and optional pilot inputs. The
[pilot workflow](pilot-workflow.md) explains technical assembly and validation.
Step 12A.3 supersedes the original source-verification gaps: supplied academic
records are user-confirmed university-sourced information and must be reused
without requesting verification again. Source labels remain unchanged for
traceability. Missing date/stream applicability and numerical policy parameters
are separate scheduling inputs. The gap report includes the later student-count
workbook and confirmed continuous-lecture rule.

`templates/` contains twelve plain UTF-8 CSV worksheets plus a separate
[field-level data dictionary](templates/data-dictionary.csv). They contain no
real records or synthetic examples. The eight fixed policy-key rows identify
questions only; their decisions, dates and confirmations are blank.

| Worksheet | What to complete |
| --- | --- |
| `offerings.csv` | New authoritative offering IDs and streams; retain prefilled module reference fields and exact original worksheet/row |
| `student-groups.csv` | Group IDs, specialization, study year/semester, stream, positive count and parent |
| `population-evidence.csv` | Referenced aggregate DISJOINT/OVERLAPPING relationships; a verified disjoint partition can cover many groups |
| `session-requirements.csv` | Stable requirement IDs, offering/group, type, duration/frequency and confirmed content/combination exceptions |
| `staff-crosswalk.csv` | Existing Staff ID → ADS UUID; confirmed workload band and appointment separately |
| `staff-availability.csv` | One row per included staff/day with explicit allowed status, windows and blocked periods |
| `venue-calendars.csv` | One row per included bookable venue/day with explicit windows and blocks |
| `venue-relationships.csv` | Explicit combined-room components or shared-resource relationships |
| `preference-approvals.csv` | Scope/weights for retained preference IDs; no new preference statements are invented |
| `time-of-day-windows.csv` | Approved numeric MORNING/AFTERNOON boundaries when those labels are used |
| `fairness-cohorts.csv` | Approved explicit cohorts and reasons, if not already present in reviewed policy context |
| `policy-confirmations.csv` | Eight applicability/approval decisions referencing the reviewed policy configuration; lunch dates/streams |

Use **a new private review copy**, not the repository templates, for completed
data. A prefilled copy has been created at
`../local-data/campus-collection-step12a1/`: 184 module references, 110 staff
references, 62 venue references and 594 preference references. Unknown offering
IDs, streams, counts, hours, dates, weights and confirmations remain blank.
Original workbooks and accepted teaching assignments are unchanged. Review
copies and source records must stay out of Git; `local-data/` is already ignored.

## Filling rules

* Keep IDs as **text**, including leading zeros, punctuation and case. Do not
  replace existing IDs or let Excel auto-convert them. Import CSV columns as
  text when using Excel. Save UTF-8 CSV with original headers.
* Study year means **1–4**, not calendar year; semester is **1–2**. Stream is
  exactly `WEEKDAY` or `WEEKEND`. Session type is `LECTURE`, `TUTORIAL`, `WORKSHOP`
  or `LAB`. Counts, durations and weekly frequency must be positive integers.
* Dates use `YYYY-MM-DD`. Times use `HH:MM-HH:MM`; multiple windows use semicolons,
  for example `09:00-11:00;14:00-15:00` (**synthetic format example only**).
  End `24:00` is supported; start `24:00` is not. Intervals are half-open.
* Blank calendars/blocks mean **unknown**. `NONE` means explicitly confirmed
  no windows/blocks. Declare every configured day, including closed days.
  `Teaching Allowed=FALSE` requires `Available Windows=NONE`. Do not infer
  opening hours or treat preferences as approved availability.
* Lists of IDs/dates/streams use semicolons. Do not add spaces to identifiers.
  Group IDs alone never prove disjoint populations. Prefer aggregate evidence;
  this pack does not request student names, emails or individual identifiers.
* Every data row needs source and confirmation references plus `Valid From`/
  `Valid Until` covering the full requested academic week. These dates authorize
  that export; they do not extend an appointment's actual effective dates.
  `UNVERIFIED` remains blocked; the code does not manufacture confirmations.
* In `staff-crosswalk.csv`, a blank Appointment is an explicit declaration of
  no applicable HOD/DEAN override in this pilot profile. Check ADS appointments;
  never derive an appointment from rank or access role. Workload Band values:
  `INSTRUCTOR`, `ASSISTANT_LECTURER`, `LECTURER`, `SENIOR_LECTURER`, `DR`,
  `ASSISTANT_PROFESSOR`, `PROFESSOR`.
* Optional content reference/combination permission may be blank, inheriting
  the explicitly confirmed global policy. Confirming combination never supplies
  population evidence or common staff eligibility automatically.
* Missing venue relationship rows do not resolve the two unknown combined
  spaces. Retain their existing exclusion until explicit relationships exist.
* Optional preference/day-part/cohort worksheets may be header-only **only if**
  reviewed existing contracts already provide their required information, or
  an explicit applicable policy permits no such records. They do not silently
  disable the Step 12A full-policy profile.

## Policy confirmation keys

| Key | Decisions / required interpretation |
| --- | --- |
| `workload` | APPLY: confirm pilot applicability/classification; known teaching/admin amounts are not requested again |
| `preferences` | APPLY or NONE; NONE cannot discard existing preferences and requires approved absence/deferment for the pilot |
| `fairness` | APPLY: confirm cohorts, target verification, policy applicability and existing objective priorities |
| `administration_research` | APPLY or NONE; NONE must agree with empty applicable appointment/protection records, not erase them |
| `combination` | ALLOW or DISALLOW; preserve all original requirements and accepted staff eligibility |
| `consecutive` | APPLY: approve supplied soft-scoring parameters; no compulsory break is invented |
| `student_lunch` | APPLY: provide explicit Applicable Dates/Streams or explicitly approved NONE sets; fixed year-specific times stay unchanged |
| `venue_relationships` | APPLY or NONE; NONE requires no supplied combined/shared relationships and leaves unresolved rooms excluded |

For workload/preferences/fairness/consecutive, `Policy Reference` must match the
corresponding reviewed base contract's `policy_reference`. Other rows reference
the approval/export covering that area; the reference is retained as provenance.
Approval rows do not create missing policy JSON, weights, periods or appointments.

No new solver constraints/objectives, database writes, APIs or travel optimization
are part of this collection workflow.

Verification: **2,040 tests passed** (all 1,987 previous tests plus 53 new
collection tests); `pip check` reported no broken requirements. The synthetic
pilot CSV demonstration passed readiness with CP execution disabled. The existing
Starlette/httpx TestClient deprecation warning remains. Source hashes verified
all pre-existing code, tests, datasets and reports unchanged; only the service
README was updated among pre-existing files.
