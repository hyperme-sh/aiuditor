# Bounded permission dreaming

Conductor schedules one batch per hour over the same-user Visor socket. Each attempt has a 45-second deadline, coalesces missed ticks, and stops with its managed process. There is no catch-up queue or model-routing policy activation.

Visor persists the pending review ID before calling Host, which supplies the configured machine identity to Conductor over stdin. Conductor and AIuditor recheck the active Plane context. Requests contain at most eight records and 128 KiB, with action metadata and hashes only. AIuditor serializes review with manual policy changes, retains the batch result, and applies only personal narrowing. A duplicate ID with different contents is refused. Stale revisions are reported as superseded.

Results are retain, narrow to per-use verification, expire, or revoke. Ordinary advisories do not block the original action. Optional heuristic changes respect `dream_protected` on configured policy rules; explicit denial and required per-use verification still win. An existing personal limit is preserved when combined narrowing is unsupported. Broadening always returns through fresh joint verification.

Visor removes only the unchanged reviewed local rule and stores before/after scope, source review, reason and status. An interrupted attempt resumes its original ID; a removed or changed local rule is never restored. History is bounded to 128 records and 2 MiB and shown in the Permissions view for the current principal, organization and workspace.

Related: Conductor #70. Unit and contract checks use local disposable files and named owner substitutes. No release or version change is included.
