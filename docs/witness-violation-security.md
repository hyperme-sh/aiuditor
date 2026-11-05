# Witness violation and upstream-header security

Adoption record: [AIuditor #19](https://github.com/hyperme-sh/aiuditor/issues/19),
source Panopticon #716 at audited snapshot
`a78e267be69af4a4fd49d2dc406b2478279fc7af`.

Contract violation filing and evaluation enforce write authority before mutating
contracts, invoking upstreams or publishing events. Hydrating signed contracts
into a witness never grants write authority. Upstream calls forward only the
actor/role header allowlist; HTTPX constructs Host and framing for the actual
upstream request. Cookies and arbitrary inbound headers are excluded.

`integration/test_witness_owner_integration.py` checks persisted witness refusal
for both routes, with and without a lease binding. It reloads persisted state and
asserts that no violation, event or upstream call was produced. Its two loopback
HTTP cases verify the outbound authority headers, destination Host, body length
and stop/refund payloads against a local socket server.

Run these with the existing `scripts/check-postgres.sh` qualification lane and a
disposable PostgreSQL database. Static crawl metadata is selected only within
these tests. Default production/test approval policy settings are unchanged.
These checks establish local owner and socket behavior, not deployed witness,
live caller authentication or provider enforcement acceptance. Keep those
separate from the component adoption tracked in #19 and #3.
