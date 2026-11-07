"""Receipt checks shared by the in-memory and PostgreSQL snapshot transactions."""


class SnapshotConflict(ValueError):
    """Refuse stale state or a parent claim over an existing local row."""


def replay_receipt(previous, exported_at, snapshot_sha256):
    if previous is None:
        return False
    if exported_at < previous['exported_at']:
        raise SnapshotConflict('Parent snapshot is older than the accepted receipt')
    if exported_at == previous['exported_at']:
        if snapshot_sha256 != previous['snapshot_sha256']:
            raise SnapshotConflict('Parent snapshot changes an accepted timestamp')
        return True
    return False
