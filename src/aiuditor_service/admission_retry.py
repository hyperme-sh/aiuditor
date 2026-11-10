"""Explicit, audited retries; failed evidence and human verdicts are never rewritten."""
from datetime import UTC, datetime
import hashlib
import json
import re
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
from aiuditor_service.admission_reporting import Assignment, mutate_admission
from aiuditor_service.domain.models import Admission, AdmissionStatus, AdmissionVerdict


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def failed_snapshot(value):
    try:
        snapshot = Admission.model_validate(value)
        assignment = Assignment.model_validate(snapshot.lab_assignment)
    except ValidationError:
        raise HTTPException(409, 'Complete failed lab attempt required') from None
    if (snapshot.status is not AdmissionStatus.DENIED or snapshot.verdict is not AdmissionVerdict.DENY
            or snapshot.resolved_by != 'sandbox' or snapshot.resolved_at is None
            or re.fullmatch(r"[a-f0-9]{64}", snapshot.sandbox_report_sha256 or "") is None
            or snapshot.lab_attempt_history or assignment.admission_id != snapshot.id
            or assignment.tenant_id != snapshot.tenant_id or assignment.file_hash != snapshot.file_hash
            or assignment.class_id != snapshot.intended_class
            or (snapshot.sandbox_soak_ok is not False and snapshot.sandbox_egress_ok is not False)):
        raise HTTPException(409, 'Only an authenticated machine-denied lab attempt may be retried')
    return snapshot


def validate_history(row):
    requests = set()
    jobs = set()
    for entry in row.lab_attempt_history:
        if set(entry) != {'request_id','requested_by','requested_at','source_ref','snapshot','snapshot_sha256'}:
            raise HTTPException(409, 'Admission retry history differs')
        request = str(uuid.UUID(entry['request_id']))
        snapshot = failed_snapshot(entry['snapshot'])
        assignment = Assignment.model_validate(snapshot.lab_assignment)
        if (snapshot.id != row.id or snapshot.tenant_id != row.tenant_id
                or snapshot.file_hash != row.file_hash or snapshot.intended_class != row.intended_class
                or hashlib.sha256(canonical(entry['snapshot'])).hexdigest() != entry['snapshot_sha256']
                or request in requests or assignment.job_id in jobs
                or not entry['requested_by'] or not entry['source_ref']
                or datetime.fromisoformat(entry['requested_at']).tzinfo is None):
            raise HTTPException(409, 'Admission retry history binding differs')
        requests.add(request);jobs.add(assignment.job_id)
    return row.lab_attempt_history


def begin_failed_retry(persist, tenant, admission, body, actor, *, now=None):
    """Lock the owner row; append the complete old receipt before clearing current state."""
    if not actor or actor == 'sandbox':
        raise HTTPException(403, 'Current interactive resolver required')
    def begin(row):
        if row is None:
            raise HTTPException(404, 'Admission not found')
        history = validate_history(row)
        for entry in history:
            if entry['request_id'] == str(body.request_id):
                previous = entry['snapshot']
                if (entry['requested_by'] != actor or entry['source_ref'] != body.source_ref
                        or previous['lab_assignment']['job_id'] != str(body.expected_job_id)
                        or previous['sandbox_report_sha256'] != body.expected_report_sha256):
                    raise HTTPException(409, 'Retry request already binds different facts')
                return row
        if len(history) >= 16:
            raise HTTPException(409, 'Admission retry limit reached')
        snapshot = row.model_dump(mode='json', exclude={'lab_attempt_history'})
        previous = failed_snapshot(snapshot)
        if (previous.lab_assignment['job_id'] != str(body.expected_job_id)
                or previous.sandbox_report_sha256 != body.expected_report_sha256):
            raise HTTPException(409, 'Expected failed attempt differs')
        entry = {'request_id':str(body.request_id),'requested_by':actor,
                 'requested_at':(now or datetime.now(UTC)).isoformat(),'source_ref':body.source_ref,
                 'snapshot':snapshot,'snapshot_sha256':hashlib.sha256(canonical(snapshot)).hexdigest()}
        return row.model_copy(update={'lab_attempt_history':[*history,entry],
            'lab_assignment':None,'sandbox_report_sha256':None,'sandbox_soak_ok':None,
            'sandbox_egress_ok':None,'sandbox_egress_reason':None,'verdict':None,
            'resolved_by':None,'resolved_at':None,'status':AdmissionStatus.QUARANTINED,
            'reason':'awaiting explicit lab retry'})
    return mutate_admission(persist,tenant,admission,begin)
