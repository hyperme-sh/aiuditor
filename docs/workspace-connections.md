# Workspace connectors

AIuditor records which workspace can use an Odoo company or an Outlook mailbox
and which person, skill, or schedule can use it. The broker owns the mapping
from an opaque connection and target ID to provider endpoints and credentials.
The model receives neither the broker signing key nor a provider credential.
Registering a scope does not provision or test connectivity with its provider.

## Workspace management

All paths below are relative to `/api/v1/aiuditor/workspace-connections`.
Workspace IDs are User organization UUIDs. Management requires a verified
interactive Portal tenant session. AIuditor checks current User authority for
the requested organization before accessing connector state. An optional
`X-Tenant-ID` only cross-checks the verified session.

| Request | Required authority | Input |
| --- | --- | --- |
| `GET /` | `organization.read` | `workspace_id`, optional `after` ID and `limit` |
| `POST /` | `organization.admin` | `workspace_id`, `connection_id`, `provider`, `target` |
| `POST /{connection_id}/disable` | `organization.admin` | `workspace_id` query |
| `GET /{connection_id}/grants` | `organization.admin` | `workspace_id`, optional `after` ID and `limit` |
| `POST /{connection_id}/grants` | `organization.admin` | `workspace_id`, `grant_id`, `actor_kind`, `actor_id`, `operations`, `expires_at` |
| `POST /{connection_id}/grants/{grant_id}/revoke` | `organization.admin` | `workspace_id` query |

For the collection root, omit the trailing slash. List responses are arrays
ordered by ID. The default limit is 50 and the maximum is 100. A connection
reports `enabled` or `disabled` for its policy state. A grant reports `active`,
`expired`, `revoked`, or `connection_disabled`. An exact registration retry
returns the current record. Reuse of an ID for a different scope returns 409.
Revocation and disable are idempotent. Use a new ID to create a new grant or
connection after revocation. Grant expiry is future Unix time in seconds.

## Broker authorization

The broker calls `POST /authorize` immediately before an operation. It must
resolve the tenant, scope, principal, and run independently of the model's
arguments. Set `AIUDITOR_WORKSPACE_CONNECTION_KEY` to a dedicated secret of at
least 32 UTF-8 bytes on the broker and AIuditor. With no configured key, AIuditor
refuses every broker call. Use TLS and keep the key in broker and service secret
configuration.

Sign the exact UTF-8 JSON body with HMAC-SHA256, prepending the byte sequence
`aiuditor.workspace-connection.v1\n` where `\n` is a newline. Send the
lowercase hex digest as `X-Workspace-Connection-Signature: sha256=<digest>`.
The signed message contains these fields.

| Field | Expected value |
| --- | --- |
| `version` | `1` |
| `request_id` | Fresh UUID per operation attempt |
| `issued_at` | Unix time in seconds, at most 60 seconds old and no more than 5 seconds ahead |
| `tenant_id`, `workspace_id`, `connection_id`, `grant_id` | UUIDs that identify the exact owner and grant |
| `provider` | `odoo` or `outlook` |
| `target` | Opaque company or mailbox identifier matching the registered scope |
| `operation` | `read`, `draft`, `update`, or `send` |
| `actor_kind`, `actor_id` | `person`, `skill`, or `schedule` and the matching opaque principal identifier |
| `run_id` | Run UUID, required for skills and schedules. A person may send `null`. |

An opaque identifier is 1 to 128 characters from letters, digits, `.`, `_`, `:`,
and `-`. Use a broker-issued reference instead of a mailbox address or URL.
Operations are independent. A `draft` grant does not implicitly permit `read`,
record updates, or sending. An interactive grant does not authorize a scheduled
run. Scheduled calls must use the explicitly granted schedule principal.

The response has `version`, `request_id`, `allowed`, and `reason`. The request
ID is also the receipt ID. A 200 response allows one operation only when the
version and request ID match, `allowed` is `true`, and `reason` is `allowed`.
Deny on a mismatch, timeout, malformed response, non-success status, or reason
of `scope_mismatch`, `inactive`, `operation_denied`, or `replayed`. Never reuse
an allow result. A retry requires a fresh ID and a new decision.

Authorization checks the live grant and records the decision before returning.
Revocation disables later decisions. A provider operation already dispatched
continues under the provider's own guarantees. Receipts store scope IDs, run
ID, operation, provider, decision, and time. They omit credentials, provider
addresses, target labels, raw actor IDs, tool arguments, and message or prompt
contents. The broker owns provider operation receipts and their correlation to
the decision ID.
