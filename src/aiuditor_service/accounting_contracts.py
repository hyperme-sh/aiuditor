"""Provider-neutral accounting inputs owned by the AIuditor consumer."""
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

BackendName = Literal['internal', 'none', 'http']


class AccountingBinding(BaseModel):
    accounting_backend: BackendName = 'internal'
    accounting_ref: str | None = Field(default=None, min_length=1, max_length=256)
    # Historical wire input remains readable during consumer migration.
    rail: str | None = Field(default=None, max_length=32, json_schema_extra={"deprecated": True})

    @model_validator(mode='before')
    @classmethod
    def legacy_backend(cls, value):
        if isinstance(value, dict) and value.get('rail') is not None:
            value = dict(value)
            value.setdefault('accounting_backend', 'http')
        return value


class Capabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')
    supports_settlement: bool = Field(strict=True)


class SettlementStatus(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: uuid.UUID
    accounting_ref: str
    overdue: bool = Field(strict=True)
