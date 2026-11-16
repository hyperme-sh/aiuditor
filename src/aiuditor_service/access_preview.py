"""Read-only access policy preview; never issues an execution grant."""
from __future__ import annotations
import hashlib
import json
import re
import time
import uuid
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('version', 'template_version', mode='before', check_fields=False)
    @classmethod
    def integer_version(cls,value):
        if type(value) is not int:raise ValueError('Integer version required')
        return value


class Limits(Strict):
    max_bytes: int = Field(strict=True, ge=1, le=67108864)
    timeout_seconds: int = Field(strict=True, ge=1, le=300)


Operation = Literal['read', 'write', 'https', 'service_inspect', 'service_restart']


class Action(Strict):
    operation: Operation
    target: str = Field(min_length=1, max_length=4096)
    arguments_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    limits: Limits

    @field_validator('target')
    @classmethod
    def printable(cls, value):
        if any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Invalid target')
        return value

    def normalized(self):
        target = self.target
        if self.operation in ('read','write'):
            # Lexical preview only. The executor must separately bind the OS
            # object; this must never be mistaken for symlink-safe execution.
            if not target.startswith('/') or any(p in ('.','..') for p in target.split('/')) or any(c in target for c in '*?\\'):
                raise ValueError('Absolute explicit local target required')
            target = str(PurePosixPath(target))
        elif self.operation == 'https':
            parsed = urlsplit(target)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c in target for c in '*\\') or any(p in ('.','..') for p in parsed.path.split('/')):
                raise ValueError('Explicit HTTPS target required')
            # Reject non-canonical spellings rather than letting languages
            # disagree about authority, escaping, ports or path normalization.
            if parsed.hostname != parsed.hostname.lower() or parsed.netloc != parsed.hostname or '%' in target:
                raise ValueError('Canonical HTTPS host required')
            target = urlunsplit(('https',parsed.hostname,parsed.path or '/','',''))
        elif not re.fullmatch(r'[A-Za-z0-9._@-]{1,128}',target):
            raise ValueError('Named service required')
        return self.model_copy(update={'target':target})


class TemplateRule(Strict):
    operation: Operation
    target: str = Field(min_length=1,max_length=4096)
    limits: Limits


class Preview(Strict):
    version: Literal[1]
    request_id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    bot_id: uuid.UUID
    assignment_id: uuid.UUID
    session_id: uuid.UUID
    project: str = Field(min_length=1,max_length=4096)
    template_version: Literal[1]
    template: list[TemplateRule] = Field(min_length=1,max_length=32)
    action: Action


class PolicyRule(TemplateRule):
    effect: Literal['allow','advisory','deny','extreme']
    reason: str = Field(min_length=1,max_length=256)
    no_always_allow: bool = Field(strict=True)
    category: Literal['ordinary','credential_exfiltration','destructive_system_control','security_control_disable']


class OrganizationPolicy(Strict):
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    rules: list[PolicyRule] = Field(max_length=128)


class Policy(Strict):
    version: Literal[1]
    revision: uuid.UUID
    organizations: list[OrganizationPolicy] = Field(max_length=128)

    def validate_rules(self):
        seen=set()
        for org in self.organizations:
            key=(org.tenant_id,org.organization_id)
            if key in seen:raise ValueError('Duplicate organization policy')
            seen.add(key)
            for rule in org.rules:
                canonical=Action(operation=rule.operation,target=rule.target,arguments_sha256='0'*64,limits=rule.limits).normalized()
                if canonical.target!=rule.target:raise ValueError('Non-canonical policy target')
                if rule.effect=='extreme' and rule.category=='ordinary':raise ValueError('Extreme reason category required')
        return self


def evaluate(body: Preview, fact: dict, policy: Policy, now=None):
    now=time.time() if now is None else now
    for field in ('tenant_id','device_id','bot_id','assignment_id','session_id'):
        if fact.get(field)!=str(getattr(body,field)):
            raise ValueError('Authenticated context mismatch')
    if fact.get('version')!=1 or not isinstance(fact.get('principal'),str) or not fact['principal'] or not now < fact.get('expires_at',0) <= now+11:
        raise ValueError('Authenticated context expired')
    org=uuid.UUID(fact['organization_id'])
    action=body.action.normalized()
    if not body.project.startswith('/') or str(PurePosixPath(body.project))!=body.project or any(p in ('.','..') for p in body.project.split('/')):
        raise ValueError('Canonical project path required')
    policy.validate_rules()
    selected=next((p for p in policy.organizations if p.tenant_id==body.tenant_id and p.organization_id==org),None)
    rules=[] if selected is None else [r for r in selected.rules if r.operation==action.operation and r.target==action.target]
    personal=[]
    for r in body.template:
        canonical=Action(operation=r.operation,target=r.target,arguments_sha256='0'*64,limits=r.limits).normalized()
        if canonical.target!=r.target:raise ValueError('Non-canonical template target')
        if r.operation==action.operation and r.target==action.target:personal.append(r)
    decision='unavailable' if selected is None else 'prohibited'
    reason='Organization policy unavailable' if selected is None else 'No matching organization permission'
    permitted=False
    if any(r.effect=='extreme' for r in rules):
        decision='extreme';reason=next(r.reason for r in rules if r.effect=='extreme')
    elif any(r.effect=='deny' for r in rules):
        decision='prohibited';reason=next(r.reason for r in rules if r.effect=='deny')
    elif rules and not personal:
        decision='prohibited';reason='Outside selected personal template'
    elif rules and personal:
        permitted=True
        decision='advisory' if any(r.effect=='advisory' for r in rules) else 'allowed'
        reason=next((r.reason for r in rules if r.effect=='advisory'),rules[0].reason)
    limits=Limits(max_bytes=min([action.limits.max_bytes]+[r.limits.max_bytes for r in rules+personal]),timeout_seconds=min([action.limits.timeout_seconds]+[r.limits.timeout_seconds for r in rules+personal]))
    if permitted and (limits.max_bytes<action.limits.max_bytes or limits.timeout_seconds<action.limits.timeout_seconds):
        permitted=False;decision='prohibited';reason='Requested limits exceed current constraints'
    return dict(version=1,request_id=str(body.request_id),preview_only=True,
        identity={k:fact[k] for k in ('tenant_id','owner_id','principal','organization_id','device_id','bot_id','assignment_id','session_id')},
        action=action.model_dump(mode='json'),project=body.project,decision=decision,permitted=permitted,
        reason=reason,limits=limits.model_dump(),policy_revision=str(policy.revision),
        learning_eligible=permitted and not any(r.no_always_allow for r in rules),
        expires_at=fact['expires_at'])
