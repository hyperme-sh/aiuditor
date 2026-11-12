"""Packaged public rules and tenant-private runtime overlays."""
import json
import os
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Rule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,63}$')
    prompt_class: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
    purpose: Literal['renter', 'ir', 'service'] | None = None
    action: Literal['hold', 'deny']
    review_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class RulePack(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    audience: Literal['public', 'private']
    tenant_id: uuid.UUID | None = None
    revision: int = Field(ge=1)
    rules: list[Rule] = Field(max_length=256)

    @model_validator(mode='after')
    def scope(self):
        if (self.audience == 'private') != (self.tenant_id is not None):
            raise ValueError('Private packs require a tenant; public packs cannot contain one')
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError('Rule IDs must be unique')
        return self


def public_pack():
    pack = RulePack.model_validate_json(files(__package__).joinpath('public.json').read_bytes())
    if pack.audience != 'public':
        raise ValueError('Packaged rules must be public')
    return pack


def load_packs(tenant_id):
    try:
        packs = [public_pack()]
        directory = os.environ.get('AIUDITOR_PRIVATE_RULES_DIR')
        if directory:
            # Caller cannot supply a filename. Read a bounded file for its verified tenant.
            path = Path(directory) / (str(uuid.UUID(str(tenant_id))) + '.json')
            with path.open('rb') as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                raise ValueError('Private pack exceeds limit')
            pack = RulePack.model_validate_json(raw)
            if pack.audience != 'private' or pack.tenant_id != tenant_id:
                raise ValueError('Private rule tenant mismatch')
            packs.append(pack)
        return packs
    except (OSError, ValueError):
        raise HTTPException(503, 'Audit rule configuration unavailable') from None


def constraints(tenant_id, *, purpose, prompt_class):
    actions = set()
    for pack in load_packs(tenant_id):
        for rule in pack.rules:
            if rule.prompt_class == prompt_class and rule.purpose in {None, purpose}:
                actions.add(rule.action)
    return actions
