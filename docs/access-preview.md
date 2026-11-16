# Permission preview, version 1

`POST /api/v1/aiuditor/access/preview` is read-only. It neither executes an action
nor issues a grant. The enrolled machine bearer and matching tenant header are
checked through Plane's current bot/machine context, using the configured HTTPS
`AIUDITOR_ACCESS_PLANE_URL`. Caller-supplied owner or organization fields are
rejected. `AIUDITOR_ACCESS_CA_FILE` optionally supplies the operator's trusted CA.
The endpoint does not accept renter keys, raw user JWTs or human approval tokens.

The request binds a request UUID, tenant/device/bot/assignment/session UUIDs,
project path, template version, scoped template rules and a concrete action.
Each rule/action names an operation and exact target with byte/time limits. The
action includes an argument SHA-256 rather than arguments or prompt content.
Operations are `read`, `write`, `https`, `service_inspect` and `service_restart`.
Local paths are absolute lexical paths; the preview is not an OS object or
symlink verification. HTTPS targets are explicit canonical named destinations,
without credentials, query strings or fragments. Service targets are names,
never shell commands. The later execution slices own OS resolution and use.

The deployment-managed `AIUDITOR_ACCESS_POLICY_FILE` is a bounded JSON document:
`version: 1`, a UUID `revision`, and `organizations`. Each organization row has
`tenant_id`, `organization_id` and `rules`. Each rule contains `operation`,
`target`, `limits` (`max_bytes`, `timeout_seconds`), `effect`, `reason`,
`no_always_allow` and `category`. The file is loaded on each preview; malformed or
missing configuration never becomes an implicit allow. Deployment tooling should
replace it atomically and assign a new revision for each change. This preview
slice introduces no policy-editing or grant-mutation API; those belong to P4.

Effects are `allow`, `advisory`, `deny`, and `extreme`. Policy-defined extreme
categories are `credential_exfiltration`, `destructive_system_control`, and
`security_control_disable`; `ordinary` is not an extreme category. These are
explicit operator policy classifications, not risk labels accepted from a
caller or automatic content inference. For example, an operator may mark an
exact security-agent restart target as `security_control_disable`, with a reason
explaining why that target is prohibited. Ordinary advisories remain permitted
when the selected personal template and effective limits permit the action.

An explicit organization deny or extreme rule overrides the personal template.
No matching organization rule prohibits the action; no organization policy
reports unavailable. A personal template may narrow access but cannot expand the
organization's permissions or limits. `no_always_allow` makes learning ineligible
without turning an otherwise permitted advisory into a denial. Existing grants
are untouched. Responses include the authenticated identity, normalized action,
effective limits, policy revision, reason and expiry, with `preview_only: true`.
Verification failures use a fixed public error and no credentials or raw upstream
errors. Neither these results nor a remembered template authorize execution.
