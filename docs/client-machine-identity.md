# Owner-attested client machines

Plane #151 uses the existing Ed25519 proof format, nonce window, rotation overlap,
short-lived token format and durable authority rows. It calls `client-bind`,
`client-token` and `client-introspect` below `/api/v1/aiuditor/machine` using the
existing authenticated control-plane identity. These are internal attestation
operations, not mobile-facing APIs. The plane must recheck the current owner,
tenant, device and enrollment before each operation.

`client-configuration` returns the existing authority/control-plane identifiers
plus `client_policy: owner-attested-v1`. Consumers must check this capability
before enrollment. The immutable policy is set by the authenticated plane at
first binding. Ordinary host bind/token, direct renew, host introspection and
contract-token operations cannot use or convert a client identity. Host behavior
and its wire format remain unchanged. The existing revoke operation retires both
kinds of identity. No new manual credentials or signing releases are introduced.

Unit tests exercise real signatures, replay, key rotation, lost-response retry,
policy separation and expiry. Route tests substitute an in-memory SQL transaction
and authenticated-plane lookup; they do not establish deployed service behavior.
