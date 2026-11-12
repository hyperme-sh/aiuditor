# Public and private audit rules

AIuditor's `/review` applies three layers: packaged public rules, a tenant-private
rule file when configured, and the existing tenant policy. Rules restrict the
existing policy; they cannot create an allow grant, bypass authentication, disable
human authority or introduce models outside the tenant's permitted candidates.
A matching deny or hold refuses routing. Hold currently returns a review-required
deny; it does not fabricate an inbox approval. `local_only` removes every remote
candidate, including remote mesh candidates. Multiple layers intersect.

The open-source baseline is `aiuditor_service/rules/public.json`: credential
exfiltration and authorization bypass are denied; destructive actions and personal
data export require review. These are exact structured class selectors, not a
claim that string matching detects unsafe text. The authenticated upstream supplies
the classification. Existing requests still need an explicit tenant allow policy.

Set `AIUDITOR_PRIVATE_RULES_DIR` to a deployment-owned directory outside the image
and checkout. Each verified tenant loads `<tenant UUID>.json`, with this shape:

```json
{
  "schema_version": 1,
  "audience": "private",
  "tenant_id": "00000000-0000-4000-8000-000000000001",
  "revision": 1,
  "rules": [
    {"id": "local-private-work", "prompt_class": "private_operation", "action": "local_only"}
  ]
}
```

The UUID above is illustrative, never a provisioned identity. Use the deployment's
existing tenant. Optional `purpose` restricts a rule to renter, service or ir.
Configured missing, invalid, oversized or cross-tenant files return 503, rather
than dropping private restrictions. Unconfigured private storage means the public
baseline and existing tenant policy only. Private business profiles and operating
instructions belong in the private operator repository, not this package.

## Review and reward loop

Reviews run where the logs are authorized to reside. Convert each reviewed
prompt-log trace or dream observation into a body-free feedback record:

```json
{
  "review_id": "00000000-0000-4000-8000-000000000002",
  "tenant_id": "00000000-0000-4000-8000-000000000001",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "source": "prompt_log",
  "purpose": "service",
  "prompt_class": "private_operation",
  "action": "local_only",
  "reward": 1.0,
  "propensity": 1.0,
  "split": "validation"
}
```

The trusted review process supplies a stable source digest, independent review ID,
observed action, measured outcome reward in [-1,1], and that action's logged
selection probability in [0.01,1]. Do not invent propensity or label predictions
as observed outcomes. For a deterministic logged action its propensity is 1;
there is no support for inferring outcomes of unobserved actions. Unknown/free-text
fields, prompt bodies, duplicate sources/reviews and foreign tenants are refused.
Review facts and outputs remain private. A digest is traceability, not anonymization.

The offline learner is a small contextual bandit: within each purpose/class it
selects the supported action with highest training mean reward, then evaluates
it on disjoint real prompt-log reviews using self-normalized inverse-propensity
weights. It reports effective sample size and a conservative support margin.
Positive margin and at least three effective independent validation samples are
required for activation. This is an empirical filter, not proof of improvement,
a substitute for domain reward design, or provider-model weight training.

Dream rows may train proposals but cannot validate them. Reusing a source in
both splits is refused. Conductor's existing dream proposal is not directly an
authorized AIuditor policy: review its cited traces into these records. Do not
import its `--bodies` output or treat an AIuditor denial as permission to bypass
AIuditor. A negative/sparse evaluation leaves a candidate inactive.

Generate a private proposal (output is created with mode 0600, never overwritten):

```sh
python -m aiuditor_service.rules.learning --tenant "$TENANT_ID" \
  --feedback /private/reviews.jsonl --output /private/proposal.json
```

Inspect the candidate and evaluation in that private environment. The operator
can explicitly activate from the same reviewed feedback:

```sh
python -m aiuditor_service.rules.learning --tenant "$TENANT_ID" \
  --feedback /private/reviews.jsonl --activate-directory /private/active-rules
```

Activation re-runs validation, locks the tenant file, and atomically adds rules.
It cannot remove or replace an existing rule. Repeating identical feedback is a
no-op. Each learned rule carries its feedback digest. Keep reviewed feedback and
prior configuration in private operator storage for audit and rollback; normal
configuration management may explicitly restore a reviewed prior file. Learning
never writes public rules, Git history, repository permissions or production
configuration by itself. Public promotion is an explicitly authored, sanitized
source change with synthetic tests; never copy private review outputs into a PR.

No live exploration or automatic runtime self-modification is enabled. Quality,
cost, safety and usefulness rewards must be specified by the operator for the
chosen context. Initial actions are hold, deny and local-only routing; more
expressive routing/optimization actions require separate consumer contracts and
regression checks before they can be trained or activated.
