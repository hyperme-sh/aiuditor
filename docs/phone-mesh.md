# Phone mesh authorization

The additive phone contract uses the existing owner-attested client policy.
It does not authorize model intake, host management, selling, or application
requests. Application requests still require Conductor's AIuditor decision.

An authenticated Plane calls `POST /api/v1/aiuditor/machine/client-mesh-grant`
using the existing control-plane token and tenant cross-check. Before each
call it must check current owner membership, bans, enrollment status and the
registered WireGuard public key. The body contains `device_id`, `request_id`,
the machine credential's SHA-256 `digest`, `gateway_id`, and `wg_public_key`.
The key must come from the enrollment record, never a replacement supplied by
the requesting client. AIuditor checks its current client credential and pins
that public key to the registration. Key replacement requires new enrollment.

An explicit `AIUDITOR_PHONE_GATEWAYS` JSON registry maps each gateway ID to a
dedicated `token` (at least 32 characters) and an allowed `control_planes`
list. Empty configuration refuses issuance. Gateway tokens authorize only this
verification endpoint; they are not control-plane attestation credentials.
Provision their values outside source and issue descriptions.

The response is an opaque `grant`, `gateway_id`, and absolute `expires_at`.
Grants last at most 60 seconds, never longer than the client credential, and
only hashed grants are stored. At most eight unexpired grants are retained per
registration. Plane passes the grant to the selected Gateway over its trusted
server connection. It must not expose this server capability to browser code.

Gateway calls `POST /api/v1/aiuditor/machine/client-mesh-introspect` with
`X-Phone-Gateway-ID`, `X-Phone-Gateway-Token`, `X-Tenant-ID`, and the body's
`control_plane_id`, `device_id`, `request_id`, and `grant`. An active response
binds every one of those IDs, the exact `wg_public_key`, and `expires_at`.
Revoked registrations, retired credentials, expired grants and other audiences
return inactive. Gateway must match the complete response before applying a
peer and must remove that peer no later than expiry if fresh authorization is
unavailable. A phone refresh requires a fresh Plane owner check and grant.

Gateway owns addresses and network application; AIuditor does not choose routes
or allocate addresses. No phone private key crosses either server boundary.
Gateway must independently restrict phone traffic to configured Conductor
destinations; a client-side AllowedIPs setting is not an access control.
No caller may substitute a host peer or a renter credential.

This source contract does not configure Gateway or deploy credentials. TEST
services follow their existing moving channels and tolerate the new capability
being unavailable until all consumers and configuration are present.
