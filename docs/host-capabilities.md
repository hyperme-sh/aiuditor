# Enrolled-host capability review

This contract is separate from model routing review. Capabilities start disabled.
An interactive tenant auditor/admin with current approval authority may replace
an enrolled machine's opt-in set using `PUT /api/v1/aiuditor/capabilities/policy`.
The normal verified human tenant session and current User authority checks apply;
a machine key, service token or learned rule cannot edit this policy. Witnesses
refuse policy edits and reviews. This does not change host-market consent.

Policy binds the current plane, device and registration request to an explicitly
approved WireGuard public key and at most 64 grants. Each grant names the exact
scope, owner, capability ID, revision, source SHA256 and graph SHA256, plus up to
64 approved argument SHA256 values and an expiry within one day. Hash the exact
UTF-8 JSON argument bytes dispatched to the tool, without reserialization. No
argument bodies, prompts or credentials are stored. An empty grant list disables
all calls. Policies live in AIuditor's existing registration state and are
serialized with key rotation, revocation and call review by the registration row
lock. No cross-service database ownership or migration is introduced.

`POST /api/v1/aiuditor/capabilities/review` accepts the OpenAPI
`CapabilityReview` body. Sign the UTF-8 bytes of
`hypermesh-capability-review-v1` followed by one newline and the entire body
excluding `signature`, encoded as sorted-key compact ASCII JSON (no trailing
newline). The fixed schema contains no floats or arbitrary JSON. UUIDs use
lowercase canonical form. Sign with the enrolled Ed25519 private key and encode
the signature in canonical padded base64. The signature binds the tenant,
authority, plane, device, registration, WireGuard key, full capability binding,
argument digest, call UUID, nonce, timestamps and public signing key. This is a
new signature domain; machine token proofs cannot be substituted.

Review verifies the signature before opening its tenant transaction, then checks
the current registration, revocation, current/overlap signing key and current
policy under a row lock. Calls last at most 30 seconds, allowing five seconds of
future clock skew. Nonces and call IDs cannot repeat within their validity;
allow and deny both consume them. At most 128 unexpired calls are retained.
Retries after a lost reply must fail closed: a review response is not a reusable
credential. The consumer must correlate `call_id` and `request_sha256` (SHA256
of the signing bytes), require `decision == "allow"`, and check expiry immediately
before dispatch. A changed call needs a new review. The execution owner must also
prevent duplicate execution across expired review windows and restarts.

The enrolled host signs a request to exercise its own approved capability. A
WireGuard key in JSON is an owner-approved identity binding, not proof that a
packet arrived through a tunnel. The host adapter must enforce loopback or its
WireGuard interface and authenticate actual peers; do not expose this as a public
remote execution endpoint. Use an authenticated TLS connection to AIuditor or a
verified WireGuard peer connection. Never accept a client-supplied allow result.
Conductor must submit each call and enforce denial; tool dispatch must preserve
the reviewed argument bytes and graph, acquire locks, refresh preconditions and
honor controller limits below Visor. AIuditor's allow decision cannot override an
e-stop, actuator bound, geofence or local refusal.

This service change supplies the policy and review endpoints only. Consumer
wiring, interface enforcement and physical-controller checks remain separate
work under Visor #99. General parameter-range grants are not implemented: the
first contract deliberately accepts exact approved argument hashes only.
