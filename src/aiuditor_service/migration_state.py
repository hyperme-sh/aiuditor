"""Resolve only the packaged AIuditor chain; no shared migration registry."""
import re
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError


def revision_missing(script, revision):
    try:
        script.get_revision(revision)
    except CommandError as exc:
        cause = exc.__cause__
        if (str(exc) == f"Can't locate revision identified by '{revision}'"
                and isinstance(cause, ResolutionError)
                and str(cause).startswith(f"No such revision or branch '{revision}'")):
            return True
        raise
    return False


def require_owner_ahead(revision, image_head, owned_tables, tables):
    # Preserve the existing adopted-deployment compatibility policy. Revision
    # numbers are not proof of order; verify-only runtimes require exact head.
    if not re.fullmatch(r'aiuditor_\d{4}_[a-z0-9_]+', revision):
        raise ValueError(f'aiuditor: unknown revision {revision} is not this owner chain (image {image_head})')
    missing = sorted(set(owned_tables) - set(tables))
    if missing:
        raise ValueError(f'aiuditor: unknown revision is missing tables this image needs: {missing}')
