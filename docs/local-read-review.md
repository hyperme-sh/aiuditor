# Current policy review for one local read

`POST /api/v1/aiuditor/access/read-review` uses the existing preview schema plus
an optional `expected_policy_revision` UUID. It authenticates the enrolled machine
and fetches Plane's active `machine/access-context`, requiring current consent and
heartbeat. It reloads the organization policy for each request. An unavailable
owner fails the request; it cannot create a remembered allow.

Only a canonical `read` target strictly inside a non-root project is accepted.
The response uses the existing action/identity/project/limits/revision fields with
`preview_only: false`; the action digest binds the consumer's resource metadata
and arguments. A remembered revision mismatch returns a prohibition. Advisories
permit the otherwise authorized read, while explicit deny or extreme policy
prevents it. `no_always_allow` prevents learning without prohibiting the read.

This response is one part of matching Conductor and AIuditor verification. Visor
must separately enforce OS permissions, reject symlink substitution and path
escape, bind the opened file, honor cancellation and check response expiry before
reading. No file contents or credentials belong in learned-rule metadata. The
service does not read files, store learned rules or widen selected templates.

Supports Conductor #68; requires Plane #177. Unit and route checks cover the policy
and active-context contract using named in-process owner substitutes.
