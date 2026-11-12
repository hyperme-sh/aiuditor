"""Shared runtime channels and compatible admission wire shapes."""
import hashlib
import json
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError
from aiuditor_service import service_channels
from aiuditor_service.private_models.contracts import AdmissionTarget, SandboxEvidence, canonical_hash

VECTORS = json.loads(Path(__file__).with_name('service-reference-vectors.json').read_text())
REPOSITORY = VECTORS['repository']


@pytest.fixture(autouse=True)
def repository_policy(monkeypatch):
    monkeypatch.setenv('AIUDITOR_RUNTIME_IMAGE_REPOSITORIES', json.dumps([REPOSITORY]))


def target(**images):
    config = {'context_tokens': 4096, 'jinja': False, 'timeout_seconds': 120}
    return dict(revision_id=uuid.uuid4(), format='gguf', owner_sub='owner', class_id='agx-large',
                backend='cuda-jetson', runtime='nvidia', configuration=config,
                configuration_sha256=canonical_hash(config), manifest_sha256='a'*64,
                inspection_sha256='b'*64, file_sha256='c'*64, size_bytes=10, license='MIT', **images)


@pytest.mark.parametrize('field', ['runtime_image', 'runtime_image_digest'])
@pytest.mark.parametrize('image', VECTORS['accepted'])
def test_new_and_old_plane_shape_preserve_channel(field, image):
    value = AdmissionTarget.model_validate(target(**{field: image}))
    assert value.runtime_image_digest == image
    assert value.model_dump()['runtime_image_digest'] == image


@pytest.mark.parametrize('image', VECTORS['rejected'])
def test_new_reference_reuses_shared_refusals(image):
    with pytest.raises(ValueError):
        service_channels.reference(image, REPOSITORY)
    with pytest.raises(ValidationError):
        AdmissionTarget.model_validate(target(runtime_image=image))


@pytest.mark.parametrize('field', ['runtime_image', 'runtime_image_digest'])
def test_unapproved_channel_is_refused_in_both_shapes(field):
    with pytest.raises(ValidationError):
        AdmissionTarget.model_validate(target(**{field: 'evil.test/runtime:test'}))


def test_old_immutable_target_is_preserved_without_new_channel_policy(monkeypatch):
    monkeypatch.delenv('AIUDITOR_RUNTIME_IMAGE_REPOSITORIES', raising=False)
    image = 'registry.example.com/runtime/model@sha256:'+'d'*64
    assert AdmissionTarget.model_validate(target(runtime_image_digest=image)).runtime_image_digest == image
    with pytest.raises(ValidationError):
        AdmissionTarget.model_validate(target(runtime_image=REPOSITORY+':test'))


def test_ambiguous_or_missing_image_is_refused():
    for images in ({}, {'runtime_image': REPOSITORY+':test', 'runtime_image_digest': REPOSITORY+':test'}):
        with pytest.raises(ValidationError):
            AdmissionTarget.model_validate(target(**images))


def test_independent_host_report_shapes_normalize_to_same_contract():
    report = dict(target_sha256='a'*64, lab_job_id=uuid.uuid4(), lab_device_id=uuid.uuid4(),
                  observed_class_id='agx-large', file_sha256='b'*64, backend='cuda-jetson',
                  configuration_sha256='c'*64, soak_passed=True, egress_ok=True, egress_reason='isolated')
    old = SandboxEvidence.model_validate(dict(report, runtime_image_digest=REPOSITORY+':test'))
    new = SandboxEvidence.model_validate(dict(report, runtime_image=REPOSITORY+':test'))
    assert old.model_dump() == new.model_dump()


def test_shared_source_binding():
    source = json.loads((Path(__file__).parents[1]/'scripts/service-channels-source.json').read_text())
    for path in (Path(service_channels.__file__), Path(__file__).with_name('service-reference-vectors.json')):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256'][path.name]
