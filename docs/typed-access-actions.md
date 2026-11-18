# Typed access actions

`POST /api/v1/aiuditor/access/action-review` extends the active read-review
contract with exact `arguments_json` bytes and their SHA-256 in the action.
Duplicate keys, unknown arguments and operation/type mismatches are refused.
This is metadata only: request contents and credentials are not sent for review.
Current active assignment, organization policy, personal template, revision and
expiry still apply. Ordinary advisories permit otherwise authorized actions.

- HTTPS binds GET or POST, the canonical destination, resolved public unicast
  addresses, request-body digest/size and limits. Matching allow/advisory rules
  must explicitly set `method`. Consumers must connect only to reviewed addresses,
  verify TLS for the named host and refuse redirects and unapproved credentials.
- Writes bind content digest/size and expected file and parent-directory identity
  digests. Only explicit files strictly within the selected project qualify.
  Consumers must enforce no-follow opens, OS permissions and atomic replacement.
- Service restart binds the named unit, non-root UID, executable path/digest and
  controller binding. Matching policy must explicitly set `executable_sha256`.
  Consumers must use the existing configured controller reservation/dispatch path,
  verify current identity and use fixed arguments without shell interpretation.

The owner-service result is not an executor or a persistent grant. Side effects
require durable duplicate suppression; the read-only retention window is not
sufficient. Existing read/preview endpoints remain compatible. Supports Conductor
#69; the cross-repository consumer is still required before that issue closes.
