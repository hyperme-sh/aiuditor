"""Monotonic narrowing of the existing scoped access policy."""
from __future__ import annotations

import uuid
from typing import Literal
from pydantic import Field, model_validator
from aiuditor_service.access_preview import Action, Limits, Policy, PolicyRule, Strict


class Restriction(Strict):
    operation: Literal['read', 'write', 'https', 'service_inspect', 'service_restart', 'check']
    target: str = Field(min_length=1, max_length=4096)
    mode: Literal['deny', 'per_use', 'limited', 'inherited']
    reason: str = Field(min_length=1, max_length=256)
    limits: Limits | None = None

    @model_validator(mode='after')
    def canonical(self):
        action = Action(operation=self.operation, target=self.target,
                        arguments_sha256='0'*64,
                        limits=self.limits or Limits(max_bytes=1, timeout_seconds=1))
        if action.normalized().target != self.target:
            raise ValueError('Canonical resource required')
        if (self.mode == 'limited') != (self.limits is not None):
            raise ValueError('Only limited rules specify limits')
        return self


class PolicyMutation(Strict):
    version: Literal[1] = 1
    organization_id: uuid.UUID
    expected_revision: uuid.UUID
    scope: Literal['personal', 'organization']
    restriction: Restriction


def restriction_key(subject: str, rule: Restriction) -> str:
    # Subject is supplied by verified owner identity, never by mutation input.
    import hashlib
    import json
    return hashlib.sha256(json.dumps([subject, rule.operation, rule.target],
                                     separators=(',', ':')).encode()).hexdigest()


def changed(state: dict, mutation: PolicyMutation, subject: str, *, admin: bool) -> tuple[dict, dict]:
    """Called while the owner holds the organization policy row lock."""
    if str(mutation.organization_id) != state['organization_id']:
        raise ValueError('Organization differs')
    if str(mutation.expected_revision) != state['revision']:
        raise ValueError('Policy changed; reload before editing')
    if not subject or len(subject) > 512:
        raise ValueError('Verified subject required')
    if mutation.scope == 'organization' and not admin:
        raise PermissionError('Organization administration required')
    owner = '' if mutation.scope == 'organization' else subject
    rule = mutation.restriction
    entries = dict(state['restrictions'])
    key = restriction_key(owner, rule)
    # An inherited entry is a retained tombstone, not deletion. Older snapshots
    # cannot reapply the removed restriction without a new authorized revision.
    entries[key] = {'subject': owner, **rule.model_dump(mode='json')}
    organization_count=sum(entry['subject']=='' for entry in entries.values())
    personal_count=len(entries)-organization_count
    own_count=sum(entry['subject']==owner for entry in entries.values())
    if (organization_count > 4096 or (owner and (own_count > 128 or personal_count > 4096))):
        raise ValueError('Policy scope capacity reached')
    revision = str(uuid.uuid4())
    sequence = state['sequence'] + 1
    updated = {**state, 'revision': revision, 'sequence': sequence, 'restrictions': entries}
    event = {'sequence': sequence, 'revision': revision, 'previous_revision': state['revision'],
             'subject': subject, 'scope': mutation.scope, 'restriction': rule.model_dump(mode='json')}
    return updated, event


def effective(base: Policy, state: dict, principal: str) -> Policy:
    """Restrictions can remove or narrow an existing allow, never create one."""
    policy = base.model_copy(deep=True)
    policy.revision = uuid.UUID(state['revision'])
    organization = next((o for o in policy.organizations
                         if str(o.tenant_id) == state['tenant_id']
                         and str(o.organization_id) == state['organization_id']), None)
    if organization is None:
        return policy
    for entry in state['restrictions'].values():
        if entry['subject'] not in ('', principal):
            continue
        rule = Restriction.model_validate({k:v for k,v in entry.items() if k != 'subject'})
        if rule.mode == 'inherited':
            continue
        matches = [r for r in organization.rules if r.operation == rule.operation and r.target == rule.target]
        if rule.mode == 'deny':
            organization.rules.append(PolicyRule(operation=rule.operation, target=rule.target,
                effect='deny', reason=rule.reason, category='ordinary', no_always_allow=True,
                limits=Limits(max_bytes=1, timeout_seconds=1)))
        else:
            for match in matches:
                if rule.mode == 'per_use':
                    match.no_always_allow = True
                else:
                    match.limits = Limits(max_bytes=min(match.limits.max_bytes, rule.limits.max_bytes),
                        timeout_seconds=min(match.limits.timeout_seconds, rule.limits.timeout_seconds))
    return policy.validate_rules()
