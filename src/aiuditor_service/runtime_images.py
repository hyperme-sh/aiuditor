"""Deployment-approved runtime channels with compatibility for stored V1 targets."""
import os
import re

from aiuditor_service.service_channels import document, reference

LEGACY = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}')


def runtime_image(value, *, legacy=False):
    # Existing V1 targets and their immutable historical results must still
    # roundtrip. New runtime_image requests use the shared tag-only contract.
    if legacy and isinstance(value, str) and LEGACY.fullmatch(value):
        return value
    raw = os.environ.get('AIUDITOR_RUNTIME_IMAGE_REPOSITORIES', '[]')
    if len(raw) > 65536:
        raise ValueError('Runtime repository policy is unavailable')
    repositories = document(raw)
    if (not isinstance(repositories, list) or not 1 <= len(repositories) <= 32
            or any(not isinstance(item, str) for item in repositories)
            or len(set(repositories)) != len(repositories)):
        raise ValueError('Runtime repository policy is unavailable')
    for repository in repositories:
        reference(repository + ":test", repository)
    for repository in repositories:
        try:
            return reference(value, repository)
        except ValueError:
            continue
    raise ValueError('Runtime image must use an approved repository and explicit tag')


def normalize_runtime_image(value):
    if isinstance(value, dict) and 'runtime_image' in value:
        if 'runtime_image_digest' in value:
            raise ValueError('Ambiguous runtime image fields')
        value = dict(value)
        value['runtime_image_digest'] = runtime_image(value.pop('runtime_image'))
    return value
