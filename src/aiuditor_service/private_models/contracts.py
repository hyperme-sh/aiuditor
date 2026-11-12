"""V1 HTTP contract for revision-specific admission; no storage credentials."""

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aiuditor_service.runtime_images import normalize_runtime_image, runtime_image

SHA256 = r"^[0-9a-f]{64}$"


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_tokens: int = Field(gt=0, le=16_777_216, strict=True)
    jinja: bool = Field(strict=True)
    timeout_seconds: int = Field(gt=0, le=1800, strict=True)


class AdmissionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: uuid.UUID
    format: Literal["gguf"]
    owner_sub: str = Field(min_length=1, max_length=512)
    class_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    backend: Literal["cuda-jetson"]
    runtime: Literal["nvidia"]
    runtime_image_digest: str = Field(max_length=256)
    configuration: ModelConfiguration
    configuration_sha256: str = Field(pattern=SHA256)
    manifest_sha256: str = Field(pattern=SHA256)
    inspection_sha256: str = Field(pattern=SHA256)
    file_sha256: str = Field(pattern=SHA256)
    size_bytes: int = Field(gt=0, le=50_000_000_000, strict=True)
    license: str = Field(min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def image_shape(cls, value):
        return normalize_runtime_image(value)

    @field_validator("runtime_image_digest")
    @classmethod
    def image_reference(cls, value):
        return runtime_image(value, legacy=True)

    @model_validator(mode="after")
    def reviewed_configuration(self):
        if canonical_hash(self.configuration.model_dump()) != self.configuration_sha256:
            raise ValueError("Configuration does not match its reviewed hash")
        return self


class AdmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admission_id: uuid.UUID
    target: AdmissionTarget
    lab_job_id: uuid.UUID
    lab_device_id: uuid.UUID


class SandboxEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_sha256: str = Field(pattern=SHA256)
    lab_job_id: uuid.UUID
    lab_device_id: uuid.UUID
    observed_class_id: str = Field(max_length=64)
    file_sha256: str = Field(pattern=SHA256)
    backend: Literal["cuda-jetson"]
    runtime_image_digest: str = Field(max_length=256)
    configuration_sha256: str = Field(pattern=SHA256)
    soak_passed: bool = Field(strict=True)
    egress_ok: bool | None = Field(default=None, strict=True)
    egress_reason: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9_]+$"
    )

    @model_validator(mode="before")
    @classmethod
    def image_shape(cls, value):
        return normalize_runtime_image(value)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["allow", "deny"]
    reason: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
