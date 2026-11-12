# Conductor review contract

`POST /api/v1/aiuditor/review` requires `X-Internal-Token` matching the dedicated
`AIUDITOR_CONDUCTOR_TOKEN` (at least 32 characters). Its tenant is fixed by
`AIUDITOR_CONDUCTOR_TENANT_ID`; `X-Tenant-ID` must match. This credential cannot
create policies or resolve human approvals. Configure it only on a trusted
conductor; candidates and provider/locality metadata are its assertions.

The OpenAPI `ReviewEnvelope` and `ReviewVerdict` schemas define this contract.
The envelope contains a request ID, the reviewed request, the chosen routing
model and the full ordered candidate list. Each candidate has `tier`, `model_id`,
`provider` and `locality` (`local` or `remote`). IDs must be unique. Protocol
provider names are not assumed to imply locality; a Hypermesh destination uses
provider `hypermesh` regardless of its transport protocol.

Operator-created policies accept optional `routing_policy`:

```json
{
  "purpose": "renter",
  "prompt_class": "parse",
  "disposition": "allow",
  "routing_policy": {
    "route": {"deny_models": [{"provider": "vendor"}]},
    "allow_external_reroute": false,
    "avoid_providers": ["another-vendor"]
  }
}
```

Use either `allow_models` or `deny_models`, with selectors containing exactly
one `model_id` or `provider`. An empty allow list denies all models. Existing
policy specificity applies: connection plus class, connection, class, purpose.
A review without an explicit winning allow rule is denied; hold is not an allow.
The original approval API's purpose defaults are unchanged.

An allow verdict contains the remaining models in `route.allow_models`.
The consumer preserves candidate order, retaining the chosen model if permitted,
otherwise selecting the first allowed candidate. It must recheck capabilities,
locality, task budgets and protocol translation for the newly selected model.
No candidate means a 4xx with the policy reason. Modify verdicts use the same
constraint schema; this service currently only emits allow/deny for reviews.

For `event: upstream_refusal`, include `refusal.model` (the exact chosen model),
`refusal.reason`, the same reviewed request, and remaining candidates excluding
the refused model. The response is deny or reroute with `allowed_models` and
matching route constraints. External reroutes require explicit permission;
local and Hypermesh candidates remain subject to all other routing constraints.
Conductor enforces attempt limits and never retries after delivering content.

The structured `conductor_review` log links tenant, request ID, request-body
SHA-256, policy ID and verdict. Prompt bodies, headers and raw refusal text are
not logged. Policy persistence adds a nullable JSON column through owner revision
0062, preserving existing policies and the frozen baseline.

Conductor's earlier envelope with only string candidate IDs lacks the metadata
needed for this contract and is refused. Its paired consumer work is tracked in
hyperme-sh/hypermesh-conductor#36; do not point an older consumer at this endpoint.
The unit reference consumer in `tests/test_routing_review.py` demonstrates a
provider constraint changing the chosen model and a refused reroute staying denied.
