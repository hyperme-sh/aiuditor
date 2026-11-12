"""Conductor wire contracts and operator routing limits."""
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelSelector(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    provider: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def has_selector(self):
        if (self.model_id is None) == (self.provider is None):
            raise ValueError('Select exactly one model ID or provider')
        return self


class RouteConstraint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allow_models: list[ModelSelector] | None = Field(default=None, max_length=128)
    deny_models: list[ModelSelector] | None = Field(default=None, max_length=128)

    @model_validator(mode='after')
    def one_mode(self):
        if (self.allow_models is None) == (self.deny_models is None):
            raise ValueError('Specify allow_models or deny_models, never both')
        return self


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    route: RouteConstraint | None = None
    allow_external_reroute: bool = Field(default=False, strict=True)
    avoid_providers: list[str] = Field(default_factory=list, max_length=128)


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tier: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=128)
    locality: Literal['local', 'remote']


class ReviewedRequest(BaseModel):
    protocol: str
    method: str
    path: str
    stream: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict


class Routing(Candidate):
    job_class: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    reason: str = ''
    suggestion_tier: str | None = None
    suggestion_accepted: bool | None = None
    prefix_sha256: str = ''


class Refusal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: Candidate
    reason: str = Field(min_length=1, max_length=1024)


class ReviewEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    event: Literal['review', 'upstream_refusal'] = 'review'
    purpose: Literal['renter', 'ir', 'service'] = 'renter'
    connection_id: uuid.UUID | None = None
    request: ReviewedRequest
    routing: Routing
    candidates: list[Candidate] = Field(min_length=1, max_length=128)
    refusal: Refusal | None = None

    @model_validator(mode='after')
    def coherent(self):
        ids = [c.model_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError('Candidate model IDs must be unique')
        chosen = Candidate.model_validate(self.routing.model_dump(include=set(Candidate.model_fields)))
        if self.event == 'review':
            if chosen not in self.candidates or self.refusal is not None:
                raise ValueError('Chosen model must match a candidate; review cannot contain a refusal')
        elif self.refusal is None or self.refusal.model != chosen or chosen.model_id in ids:
            raise ValueError('Refusal must identify the chosen model and exclude it from remaining candidates')
        return self


class ReviewVerdict(BaseModel):
    action: Literal['allow', 'deny', 'modify', 'reroute']
    reason: str
    route: RouteConstraint | None = None
    allowed_models: list[str] = Field(default_factory=list)
    body: dict | None = None
