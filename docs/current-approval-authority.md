# Current approval authority

With `USER_SCOPED_AUTHORITY_REQUIRED=1`, AIuditor forwards the verified interactive
caller's bearer to User's `approval.resolve` check over HTTPS. Configure
`USER_AUTHORITY_URL` and the optional private `USER_AUTHORITY_CA_FILE`. The reply
must bind nonce, action, tenant and subject and explicitly allow the action.
No User role SQL, caller role header or service-key impersonation grants approval.
Current ban, identity, network and tenant state remains owned by User.

The existing local interactive authentication, account classification, witness
write refusal and human-subject checks still run first. The explicit TEST waiver
for unclassified interactive accounts remains separate; machine/IR/service
credentials cannot acquire human approval authority. Missing or unavailable owner
authority, redirects and malformed facts fail closed.

The canonical Hyperme.sh network admin can resolve within its active tenant
without being assigned a substitute tenant admin/auditor role. Other-network,
other-org and inactive isolation authority does not grant that scope. This mode
is activated only with the coordinated canonical TEST generation; merging or
publishing is not shared caller acceptance.
