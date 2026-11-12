# Accounting interface

AIuditor owns grant authority, not a ledger, credits, conversion or payment-provider
operations. Contracts store `accounting_backend` (`internal`, `none`, or `http`)
and an opaque `accounting_ref`. `ACCOUNTING_BACKEND` defaults to `internal` for
new contracts. Changing it never migrates existing grants. The internal/none
consumer adapters advertise no external settlement; they do not mint funds.

## Signed commits

POST `/api/v1/aiuditor/contracts/accounting/committed` with this JSON shape:

```json
{
  "type": "accounting.committed.v1",
  "id": "00000000-0000-4000-8000-000000000001",
  "tenant_id": "00000000-0000-4000-8000-000000000002",
  "accounting_backend": "http",
  "accounting_ref": "opaque-backend-reference",
  "contract": {
    "lease_id": "00000000-0000-4000-8000-000000000003",
    "renter_subject": "renter",
    "host_subject": "host",
    "reserved_hours": 2
  }
}
```

Configure a dedicated `AIUDITOR_ACCOUNTING_CALLBACK_KEY` (at least 32 characters),
`AIUDITOR_ACCOUNTING_TENANT_ID`, and the selected backend. The key cannot equal
an internal or historical paid-checkout token. The callback is scoped to that
configured tenant and backend; X-Tenant-ID, when present, must agree.

Send `X-Accounting-Timestamp` as Unix seconds within five minutes and
`X-Accounting-Signature` as `v1=` followed by lowercase HMAC-SHA256 hex. Sign the
exact bytes `aiuditor/accounting.committed.v1\n<TIMESTAMP>\n<BODY>` with the key.
The body is limited to 64 KiB. Unknown JSON fields are refused. Retry with the
same event ID, reference and terms, generating a fresh timestamp/signature.

A commit atomically binds the backend reference to one grant. Replays return the
same grant, including its current suspended/expired state, without extending its
term. Reusing an event or reference for different terms returns 409. A named
`contract_id` must refer to a signed grant with matching terms and binding;
callbacks cannot substitute for interactive human signature on a draft. Witness
instances refuse writes. Event delivery is at least once; consumers deduplicate
by grant ID and must never infer current authority from an old notification.

The deprecated `/contracts/paid/fulfill` path accepts the same signed envelope;
static paid-checkout bearer tokens no longer grant authority. Internal generic
fulfillment cannot create a grant when the configured backend is `http`.

## Optional settlement

The narrow `AccountingBackend` protocol exposes `capabilities()` and
`settlement_status(tenant_id, accounting_ref)`. The HTTP adapter uses configured
`ACCOUNTING_URL` and `ACCOUNTING_TOKEN` (at least 32 characters), with Bearer
authentication. HTTPS is required except on loopback. Redirects are refused,
requests time out after five seconds, and responses are bounded to 64 KiB.

- GET `/capabilities` returns exactly `{"supports_settlement": true}` or false.
- GET `/settlement-status?tenant_id=<UUID>&accounting_ref=<REF>` returns exactly
  `{"tenant_id":"<UUID>","accounting_ref":"<REF>","overdue":false}`.

Boolean values must be JSON booleans. Tenant and reference must match exactly.
Missing configuration, malformed responses and outages return 503; they are not
interpreted as settled. Settlement is queried only for backends that advertise
support. Internal/none grants skip it. Paused host selling is never an unpaid
condition. Existing term/certification/deny enforcement runs independently;
settlement overdue uses the existing abuse enforcement action contract.

## Caller and storage compatibility

`/catalog/{service_id}/allocate/accounting` exposes only `funding_state`
(`pending` or `funded`) and `accounting_ref` for funding, alongside grant/allocation
identity. It omits payment IDs, amounts and test-credit flags. The funding state
comes from the lease owner, and a funded result requires a reference. It is never
inferred from possession of a grant. The original allocation endpoint retains
its deprecated fields for callers migrating to the neutral response.

Migration 0063 preserves existing rail values and maps legacy rows to the HTTP
backend without inventing references or funding. Legacy rail input remains
readable. Rail-bound grants continue publishing their frozen v1 event payloads;
neutral grants publish `aiuditor.contract.{created,signed,bound,fulfilled}.v2`,
which contains backend/reference and no rail. Consumers must subscribe to v2 for
neutral grants. Both use the existing shared topic. Grant exports use neutral
fields; historical rail data remains stored. No shared ledger package is required
by this consumer port, and no backend ownership is moved into AIuditor.
