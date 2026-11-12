"""Offline contextual-bandit proposals from reviewed, body-free feedback.

No live exploration, model-weight training, or implicit runtime activation.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import uuid
from collections import defaultdict
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from . import Rule, RulePack


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    objective: Literal['audit_safety_rbac']
    review_id: uuid.UUID
    tenant_id: uuid.UUID
    # Stable source grouping keeps repeated reviews of one trace out of both splits.
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: Literal['prompt_log', 'dream']
    purpose: Literal['renter', 'ir', 'service']
    prompt_class: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
    action: Literal['hold', 'deny']
    reward: float = Field(ge=-1, le=1, allow_inf_nan=False)
    propensity: float = Field(ge=0.01, le=1, allow_inf_nan=False)
    split: Literal['train', 'validation']


def propose(rows, tenant_id, *, minimum=3):
    """Choose highest mean-reward action per context; evaluate independent real reviews.

    Dreams can contribute training feedback, never validation. Evaluation uses
    inverse-propensity weighting and reports effective sample size, not a claim
    of causally proven improvement or a provider-model training result.
    """
    if minimum < 3:
        raise ValueError('At least three independent observations are required')
    if not rows or len(rows) > 10000:
        raise ValueError('Expected 1..10000 feedback rows')
    ids, sources = set(), {}
    grouped = defaultdict(list)
    for row in rows:
        if row.tenant_id != tenant_id or row.review_id in ids:
            raise ValueError('Foreign tenant or duplicate review')
        ids.add(row.review_id)
        # One source is one sample: repeated/synthetic copies cannot inflate support.
        if row.source_sha256 in sources:
            raise ValueError('Duplicate source or training/validation leakage')
        sources[row.source_sha256] = row.split
        if row.source == 'dream' and row.split != 'train':
            raise ValueError('Dream feedback cannot validate a proposal')
        grouped[(row.purpose, row.prompt_class)].append(row)
    rules, scores = [], []
    for (purpose, prompt_class), samples in sorted(grouped.items()):
        actions = defaultdict(list)
        for row in samples:
            if row.split == 'train':
                actions[row.action].append(row.reward)
        supported = {action:sum(values)/len(values) for action,values in actions.items() if len(values) >= minimum}
        if not supported:
            continue
        action = max(sorted(supported), key=supported.get)
        validation = [r for r in samples if r.split == 'validation' and r.action == action]
        weights = [1/r.propensity for r in validation]
        total = sum(weights)
        ess = total * total / sum(w*w for w in weights) if weights else 0
        estimate = sum(w*r.reward for w,r in zip(weights,validation))/total if total else -1
        # Bounded rewards [-1,1], conservative support-sensitive lower bound.
        lower = estimate - math.sqrt(2*math.log(20)/ess) if ess else -1
        eligible = len(validation) >= minimum and ess >= minimum and lower > 0
        rule_id = 'learned-' + hashlib.sha256(f'{purpose}:{prompt_class}'.encode()).hexdigest()[:20]
        rules.append(Rule(id=rule_id,purpose=purpose,prompt_class=prompt_class,action=action))
        scores.append(dict(rule_id=rule_id,train_reward=supported[action],validation_count=len(validation),
                           effective_samples=ess,validation_reward=estimate,lower_bound=lower,eligible=eligible))
    data = [r.model_dump(mode='json') for r in sorted(rows,key=lambda r:str(r.review_id))]
    digest = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    rules = [r.model_copy(update={'review_sha256':digest}) for r in rules]
    pack = RulePack(audience='private',tenant_id=tenant_id,revision=1,rules=rules)
    return dict(schema='aiuditor.rule-proposal.v1', tenant_id=str(tenant_id),
                feedback_sha256=digest,
                candidate=pack.model_dump(mode='json'), evaluation=scores,
                eligible=bool(scores) and all(s['eligible'] for s in scores))


def read_feedback(path):
    with Path(path).open('rb') as source:
        raw = source.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError('Feedback exceeds 8 MiB')
    return [Feedback.model_validate_json(line) for line in raw.splitlines() if line.strip()]


def activate(rows, tenant_id, directory):
    """Explicit operator installation; add constraints without weakening active rules."""
    import fcntl
    import tempfile
    proposal = propose(rows, tenant_id)
    if not proposal['eligible']:
        raise ValueError('Proposal lacks independent positive validation support')
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError('Private configuration directory must already exist')
    candidate = RulePack.model_validate(proposal['candidate'])
    target = directory / (str(tenant_id) + '.json')
    lock_path = directory / (str(tenant_id) + '.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = None
        if target.is_symlink():
            raise ValueError('Private pack cannot be a symlink')
        if target.exists():
            with target.open('rb') as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                raise ValueError('Private pack exceeds limit')
            current = RulePack.model_validate_json(raw)
            if current.audience != 'private' or current.tenant_id != tenant_id:
                raise ValueError('Existing tenant binding differs')
        rules = {r.id:r for r in current.rules} if current else {}
        changed = False
        for rule in candidate.rules:
            if rule.id in rules and rules[rule.id] != rule:
                raise ValueError('Learning cannot replace an existing rule')
            changed = changed or rule.id not in rules
            rules[rule.id] = rule
        if current and not changed:
            return current
        pack = RulePack(audience='private',tenant_id=tenant_id,
                        revision=current.revision+1 if current else 1,rules=list(rules.values()))
        fd, temporary = tempfile.mkstemp(prefix='.rules-',dir=directory)
        try:
            with os.fdopen(fd,'w') as output:
                output.write(pack.model_dump_json(indent=2)+'\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary,target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feedback',required=True)
    parser.add_argument('--tenant',type=uuid.UUID,required=True)
    parser.add_argument('--output')
    parser.add_argument('--activate-directory',help='Explicitly install validated additive private rules')
    args = parser.parse_args()
    try:
        rows = read_feedback(args.feedback)
        if bool(args.output) == bool(args.activate_directory):
            raise ValueError('Choose one output mode')
        if args.activate_directory:
            activate(rows,args.tenant,args.activate_directory)
            return
        proposal = propose(rows,args.tenant)
        # Never overwrite an active rule file or expose private output via default umask.
        fd = os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as target:
            json.dump(proposal,target,sort_keys=True,indent=2)
            target.write('\n')
    except (OSError,ValueError):
        parser.exit(2,'Feedback rejected or output unavailable; no rules activated.\n')


if __name__ == '__main__':
    main()
