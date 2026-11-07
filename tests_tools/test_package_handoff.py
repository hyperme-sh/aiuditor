"""Run the actual no-checkout publisher verifier against hostile archives."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / '.github/workflows/package.yml').read_text()
CODE = textwrap.dedent(SOURCE.split("python3 - <<'HANDOFF'\n",1)[1].split('          HANDOFF',1)[0])
WHEEL = 'aiuditor_service-0.1.0-py3-none-any.whl'
SDIST = 'aiuditor_service-0.1.0.tar.gz'


class HandoffTests(unittest.TestCase):
    def verify(self, entries, *, tamper=False, attempt='1', foreign=False):
        handoff=Path('/tmp/aiuditor-qualified-987654321-1.'+uuid.uuid4().hex[:6]);handoff.mkdir(mode=0o700)
        try:
            archive=handoff/'package.tar.gz'
            with tarfile.open(archive,'w:gz') as output:
                for name, kind in entries:
                    entry=tarfile.TarInfo(name)
                    if kind=='link':
                        entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd';output.addfile(entry)
                    else:
                        entry.size=5;output.addfile(entry,io.BytesIO(b'bytes'))
            digest=hashlib.sha256(archive.read_bytes()).hexdigest()
            if tamper:archive.write_bytes(archive.read_bytes()+b'changed')
            with tempfile.TemporaryDirectory() as target:
                env=dict(os.environ,PACKAGE_HANDOFF=str(handoff),PACKAGE_SHA256=digest,
                    GITHUB_RUN_ID='1' if foreign else '987654321',GITHUB_RUN_ATTEMPT=attempt,
                    RELEASE_ARTIFACT_DIR=target)
                result=subprocess.run([sys.executable,'-c',CODE],env=env,capture_output=True)
                return result.returncode, sorted(p.name for p in Path(target).iterdir())
        finally:shutil.rmtree(handoff)

    def test_original_bytes_and_failed_job_retry(self):
        for attempt in ['1','2']:
            code,names=self.verify([(WHEEL,'file'),(SDIST,'file')],attempt=attempt)
            self.assertEqual(code,0);self.assertEqual(names,sorted([WHEEL,SDIST]))

    def test_hostile_or_unbound_archives_are_rejected(self):
        cases=[([(WHEEL,'file'),(SDIST,'file')],{'tamper':True}),
               ([(WHEEL,'file'),(SDIST,'file')],{'foreign':True}),
               ([(WHEEL,'file'),('../escape','file')],{}),
               ([(WHEEL,'link'),(SDIST,'file')],{}),
               ([(WHEEL,'file'),(WHEEL,'file'),(SDIST,'file')],{}),
               ([(WHEEL,'file')],{})]
        for entries,options in cases:
            with self.subTest(entries=entries,options=options):
                code,names=self.verify(entries,**options)
                self.assertNotEqual(code,0);self.assertEqual(names,[])


if __name__=='__main__':unittest.main()
