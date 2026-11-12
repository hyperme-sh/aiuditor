"""Tenant policy decisions for conductor; request bodies are never logged."""
import hashlib
import json
import logging
import os
from fastapi import HTTPException
from controlplane_identity.internal_token import request_has_internal_token
import uuid
from aiuditor_service.domain.models import PolicyDisposition
from aiuditor_service.policies import select_policy
from aiuditor_service.routing_contracts import ModelSelector, RouteConstraint, ReviewVerdict, RoutingPolicy

logger = logging.getLogger(__name__)


def matches(candidate, selectors):
    return any((s.model_id is not None and candidate.model_id == s.model_id)
               or (s.provider is not None and candidate.provider == s.provider) for s in selectors)


def review(store, tenant, envelope):
    policy = select_policy(store, tenant, purpose=envelope.purpose,
        prompt_class=envelope.routing.job_class,
        connection_id=envelope.connection_id)
    rules = policy.routing_policy if policy and policy.routing_policy else RoutingPolicy()
    from aiuditor_service.rules import constraints
    pack_actions = constraints(tenant, purpose=envelope.purpose, prompt_class=envelope.routing.job_class)
    if pack_actions & {'deny', 'hold'}:
        verdict = ReviewVerdict(action='deny', reason='Audit rules deny this request or require operator review')
    elif policy is None or policy.disposition != PolicyDisposition.ALLOW:
        verdict = ReviewVerdict(action='deny', reason='An explicit allow policy is required')
    else:
        candidates = [c for c in envelope.candidates if c.provider not in rules.avoid_providers]
        if rules.route is not None:
            if rules.route.allow_models is not None:
                candidates = [c for c in candidates if matches(c, rules.route.allow_models)]
            else:
                candidates = [c for c in candidates if not matches(c, rules.route.deny_models)]
        if 'local_only' in pack_actions:
            candidates = [c for c in candidates if c.locality == 'local']
        if envelope.event == 'upstream_refusal' and not rules.allow_external_reroute:
            candidates = [c for c in candidates if c.locality == 'local' or c.provider == 'hypermesh']
        if not candidates:
            verdict = ReviewVerdict(action='deny', reason='No candidate satisfies tenant routing policy')
        else:
            ids = [c.model_id for c in candidates]
            verdict = ReviewVerdict(action='reroute' if envelope.event == 'upstream_refusal' else 'allow',
                reason='Tenant routing policy permits these models',
                route=RouteConstraint(allow_models=[ModelSelector(model_id=i) for i in ids]),
                allowed_models=ids if envelope.event == 'upstream_refusal' else [])
    logger.info('conductor_review %s', json.dumps({
        'tenant_id':str(tenant), 'request_id':envelope.request_id, 'event':envelope.event,
        'request_sha256':hashlib.sha256(json.dumps(envelope.request.body, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'policy_id':str(policy.id) if policy else None,
        'verdict':verdict.model_dump(exclude_none=True)}, sort_keys=True))
    return verdict


def require_reviewer(request):
    token = os.environ.get('AIUDITOR_CONDUCTOR_TOKEN', '')
    if len(token) < 32 or not request_has_internal_token(request, token):
        raise HTTPException(401, 'Conductor review credential required')
    try:
        tenant = uuid.UUID(os.environ.get('AIUDITOR_CONDUCTOR_TENANT_ID', ''))
    except ValueError:
        raise HTTPException(503, 'Conductor tenant is unconfigured') from None
    if request.headers.get('X-Tenant-ID') != str(tenant):
        raise HTTPException(403, 'Conductor tenant mismatch')
    request.state.tenant_id = tenant
    return tenant
