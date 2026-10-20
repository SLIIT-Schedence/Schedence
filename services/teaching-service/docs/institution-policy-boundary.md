# Institution scheduling policy boundary

The Teaching Service now supports a resolved institution policy seal attached to
the existing `SolverConfig`. It composes the current contracts; it does not own
academic masters, change database schemas or require PostgreSQL to solve.

## Resolution and approval

`PolicyRevision` identifies the institution, policy reference, revision, effective
dates and approval evidence. `resolve_snapshot_policy` creates a detached **new**
input snapshot. It retains original demand, group mapping, source provenance and
calendar, and records parent snapshot/policy digests. It applies explicitly
supplied `StaffPolicyOverride` records before sealing. It never edits the source
document or selects a campus preset implicitly.

The seal stores separate canonical JSON strings for institution defaults,
rank/appointment rules, staff overrides and effective inputs. Metadata contains
only immutable scalars (including UUID/date values). There are no mutable nested
models, sets, dictionaries or lists in `ResolvedInstitutionPolicy`. Decoded JSON
is detached. Every boundary revalidates its deterministic SHA-256 digest,
including validation-bypassing `model_copy` or low-level attribute changes.

Defaults and rank/appointment rules come from supplied existing contracts. A
staff target override preserves its underlying rank and administrative allocation;
its rule source is `STAFF_OVERRIDE`. Targets remain separate from explicit hard
caps. Staff session overrides may further restrict eligibility but cannot bypass
rank/appointment rules, source permissions, module membership or availability.
Staff day limits cannot exceed the institution ceiling. Weekly overrides must
cover all configured teaching dates; partial-week target prorating is not inferred.
Unknown ranks are not supplied by an override or parsed from display names.

## Administrator authorization contract (documented, not an endpoint)

The resolver is an internal trusted configuration boundary, not an authorization
service. Approval reference strings and content hashes are **not** credentials,
digital signatures or evidence that a caller currently has permission.

A future management adapter must authenticate through existing Cognito/Identity,
resolve an explicit institution-scoped policy-management grant, authorize the
specific operation, and record the actor, authorization decision, immutable
revision, effective dates and approval evidence. The exact policy-management
permission identifier and approval/separation-of-duties workflow require review
with Identity ownership. `teaching:generate`, an Academic Admin label, a JWT role,
HOD or Dean appointment must not automatically grant policy-edit authority.

Draft configuration must not become active just because it was saved. The adapter
must load an approved revision, compute the seal server-side, reject tenant/date
mismatches and prevent an unapproved client-supplied replacement digest or override.
This patch adds no management routes, account grants, policy tables, DDL or
production selection of current policy versions. Existing historical generation
request semantics remain `HISTORICAL_SNAPSHOT`; a future current-policy selection
adapter must use this boundary explicitly.

## Consumers and tampering checks

The input snapshot and solver configuration reference the same digest. Snapshot
replay verifies institution, applicable teaching dates and every effective context.
The generic engine checks the complete supplied context before creating a model.
Candidate and independent staff checks use the same sealed eligibility and workload
inputs; lunch/venue checks verify their bound components. Independent validation
reuses detached original inputs, not CP-SAT variable domains. Assessment projections
may omit the feature being assessed to avoid recursive validation; provided inputs
must still match. `prepare_attempt` verifies the complete snapshot before accepting
any full validation result.

New policy-bound version metadata and independent validation evidence include the
resolved digest and revision. Both repository save paths populate policy metadata;
retrieval, workload reporting and timetable summaries reject mismatched references.
Workload reports expose `resolved_policy_digest` and `institution_policy_revision`.
Timetable APIs expose those fields to institution-wide readers alongside existing
version provenance. Existing snapshot/delivery hashes and append-only history remain.

Native time budgets, random seeds, worker counts and search ordering are operational
settings and may change without changing academic policy. Calendars, delivery-mode
permissions, availability, hard caps, lunch, protected periods, objective policies
and weights remain bound. A new academic/soft-policy configuration needs a new seal;
it cannot silently replace an accepted job's policy.

## Lunch and online configuration

For a new institution lunch configuration, supply `configured_academic_years` and
each period's explicit `duration_minutes`, fixed window and study year. Periods
must be unique and cover every attending study year. Dates/streams still require
explicit applicability and provenance. Lunch times are fixed inputs, never solver
choices. No lecturer lunch or daily opening hours are invented.

Online delivery permits the configured `allowed_session_types` when its
`mode_policy_reference` identifies the approved institution policy. Such mode
permissions require a resolved seal before solving. Venue suitability/capacity
remains enforced for physical deliveries and no physical room is booked online.

### Batch online lecture day (opt-in)

`SolverConfig.batch_online_days` (`BatchOnlineDayPolicy`) lets CP-SAT choose the
online lecture day for each batch. A batch is (academic year, semester, stream)
within the one institution of the snapshot. Specialization is not part of the
key, and group names are never parsed.

- **One day per batch.** Each batch with lectures gets exactly one candidate
  weekday (Monday to Friday) from `candidate_days[stream]`. Batches may share a
  day.
- **Fully online day.** On its selected day a batch has only online lectures:
  no physical lectures, labs, tutorials or workshops, and no online
  non-lectures. The same calendar day remains physical for every other batch.
- **No empty day.** At least one lecture runs online on the selected day.
- **Physical elsewhere.** A lecture on any other day is physical and needs a
  venue.
- **Fixed online rows.** An `ONLINE` calendar row must be a candidate day. It can
  host a batch only as that batch's selected day.
- **No quotas.** There are no online percentages or physical-lecture quotas.
- **Infeasible stays infeasible.** Combinations that cannot be satisfied are
  reported as INFEASIBLE.
- **Sealed policy required.** The policy needs a resolved institution seal whose
  `policy_reference` matches the approved revision. Combined deliveries are
  not supported.
- **Validation.** The independent validator re-derives each batch's online day
  from stored booking modes. Persistence stores each booking's own mode.
- **Unsupported tools.** The pilot main-group PDF and calendar placement
  diagnostics refuse this policy rather than misreport modes.
- **Compatibility.** The field is omitted from serialized configs when unset, so
  historical snapshots and policy digests are unchanged.

Unbound historical lunch and delivery contracts retain their original initial-campus
validation rules. Their newly added optional fields are omitted when absent, so
old snapshot JSON/hashes retain their meaning. This compatibility path is explicit;
new institutions use policy-bound configuration. The lunch contract can describe
other study-year sets, but upstream academic input schemas still retain the existing
reviewed Years 1–4 / Semesters 1–2 vocabulary. Generalizing academic masters is a
separate reviewed change.

## Initial university and legacy diagnostics

No initial university values were changed: Instructor LAB-only, approved academic
session matrix, 1,200-minute soft Instructor target, six-date teaching ceiling,
separate administrative allocations, fixed year-based lunch periods and the current
experimental calendar remain exactly supplied. No four-module maximum was added.
Main/subgroup planning rules and original academic occurrences remain intact.

The older `corrected-input.snapshot.json` diagnostic lacks `appointment_permissions`.
`restore_snapshot` reports `LegacyPolicySnapshotError` with an actionable message.
It does not infer empty/default permissions, rewrite the artifact or claim exact
replay. Reviewed reconstruction must create a separate input with explicit policy
data. Genuine historical snapshots without assignment policies still replay under
their original semantics. Existing 697/704 versions and the 631 local scenario are
never migrated or overwritten by this patch.

Local verification artifacts are Git-ignored under
`local-data/institution-policy-boundary/`. The 631-domain comparison runs no native
solver and disables network connections. Its derived resolved snapshot is an input
compatibility check, not a timetable or production-approved policy revision.
