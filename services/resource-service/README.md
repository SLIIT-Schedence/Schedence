# Resource Service persistence foundation

The Resource Service owns the `resource` schema and venue inventory. This
foundation contains models and an additive migration, without an API, booking
service, availability assumptions or application startup connection.

`source_datasets` retains exact workbook checksums and provenance. `venues`
retains campus-confirmed types, capacities and supplied locations, alongside
original source values and subsequent confirmation metadata. The original
venue code is preserved; UUID primary keys are issued by this owning schema.
Examination seed venues remain unchanged and are not reused as master records.

Both inventory tables use institution-scoped keys and forced PostgreSQL RLS.
The application must set `schedence.institution_id` transaction-locally from
trusted institution context, using a restricted role without BYPASSRLS.
The migration uses the existing shared `schedence_db` configuration. Preview
with `python -m alembic -c services/resource-service/alembic.ini upgrade head --sql`.
Online migrations require explicit operational authorization and recovery checks.
No production migration runs merely by importing this package.
