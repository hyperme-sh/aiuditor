"""Typed policy review for named network, project writes and service control."""
import hashlib
import ipaddress
import json
import re
from pathlib import PurePosixPath
from typing import Literal
from pydantic import Field
from aiuditor_service.access_preview import Strict, Action, ReadReview, Policy, evaluate


class HTTPSArguments(Strict):
    method: Literal['GET', 'POST']
    addresses: list[str] = Field(min_length=1, max_length=16)
    body_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    body_bytes: int = Field(strict=True, ge=0, le=1048576)


class WriteArguments(Strict):
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    content_bytes: int = Field(strict=True, ge=0, le=1048576)
    expected_object_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parent_object_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ServiceArguments(Strict):
    verb: Literal['restart']
    executable: str = Field(min_length=1,max_length=4096)
    executable_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    unit: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.@-]{0,120}\.service$')
    uid: int = Field(strict=True,gt=0)
    controller_binding_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class TypedAction(Action):
    arguments_json: str = Field(min_length=2,max_length=16384)


class ActionReview(ReadReview):
    action: TypedAction


def arguments(action: TypedAction):
    if hashlib.sha256(action.arguments_json.encode()).hexdigest()!=action.arguments_sha256:
        raise ValueError('Exact argument digest required')
    # Duplicate object keys are ambiguous across consumers and are refused.
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate argument key')
            result[key]=value
        return result
    raw=json.loads(action.arguments_json,object_pairs_hook=pairs)
    models={'https':HTTPSArguments,'write':WriteArguments,'service_restart':ServiceArguments}
    model=models.get(action.operation)
    if model is None:raise ValueError('Typed operation required')
    result=model.model_validate(raw)
    if isinstance(result,HTTPSArguments):
        if len(set(result.addresses))!=len(result.addresses):raise ValueError('Duplicate address')
        for address in result.addresses:
            parsed=ipaddress.ip_address(address)
            if str(parsed)!=address or not parsed.is_global or parsed.is_multicast or parsed.is_unspecified or getattr(parsed,'ipv4_mapped',None):
                raise ValueError('Explicit public unicast address required')
        if result.method=='GET' and (result.body_bytes!=0 or result.body_sha256!=hashlib.sha256(b'').hexdigest()):
            raise ValueError('GET has no request body')
    if isinstance(result,ServiceArguments):
        path=PurePosixPath(result.executable)
        if not result.executable.startswith('/') or result.executable.startswith('//') or str(path)!=result.executable or '..' in path.parts or '.' in result.executable.split('/') or any(ord(c)<32 for c in result.executable):
            raise ValueError('Canonical executable required')
        if result.unit!=action.target:raise ValueError('Named service differs')
    return result


def evaluate_action(body:ActionReview,fact:dict,policy:Policy,now=None):
    action=body.action.normalized()
    if action.target!=body.action.target:raise ValueError('Canonical target required')
    args=arguments(body.action)
    if body.action.operation=='write':
        project=PurePosixPath(body.project)
        if body.project in ('/','//') or body.project.startswith('//') or action.target.startswith('//') or project not in PurePosixPath(action.target).parents:
            raise ValueError('Write must remain in selected project')
        if args.content_bytes>action.limits.max_bytes:raise ValueError('Write exceeds byte limit')
    if isinstance(args,HTTPSArguments) and args.body_bytes>action.limits.max_bytes:
        raise ValueError('Request exceeds byte limit')
    policy.validate_rules()
    effective=policy.model_copy(deep=True)
    for org in effective.organizations:
        applicable=[]
        for rule in org.rules:
            if rule.operation==action.operation and rule.target==action.target:
                if isinstance(args,HTTPSArguments):
                    if rule.method is not None and rule.method!=args.method:continue
                    if rule.effect in ('allow','advisory') and rule.method is None:continue
                if isinstance(args,ServiceArguments):
                    if rule.executable_sha256 is not None and rule.executable_sha256!=args.executable_sha256:continue
                    if rule.effect in ('allow','advisory') and rule.executable_sha256 is None:continue
            applicable.append(rule)
        org.rules=applicable
    result=evaluate(body,fact,effective,now=now)
    result['preview_only']=False
    if body.expected_policy_revision is not None and body.expected_policy_revision!=policy.revision:
        result.update(permitted=False,learning_eligible=False,decision='prohibited',reason='Remembered policy revision changed')
    return result
