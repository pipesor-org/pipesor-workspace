import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
ACTION = json.loads((ROOT / 'actions/java-verify/action.yml').read_text())
WORKFLOW = json.loads((ROOT / '.github/workflows/publish-container.yml').read_text())
JOB = WORKFLOW['jobs']['publish']
STEPS = {step.get('name'): step for step in JOB['steps']}


class ContractTests(unittest.TestCase):
    def test_fixed_maven_and_reports_contract(self):
        self.assertNotIn('inputs', ACTION)
        verify, reports = ACTION['runs']['steps']
        self.assertEqual(verify['run'], './mvnw -B -ntp clean verify')
        self.assertIn('always()', reports['if'])
        self.assertIn("env.ACT != 'true'", reports['if'])
        self.assertEqual(reports['with'], {
            'name': 'tests-${{ github.run_id }}-${{ github.run_attempt }}',
            'path': 'target/surefire-reports/TEST-*.xml',
            'if-no-files-found': 'ignore', 'retention-days': 14,
        })

    def test_publisher_has_no_caller_code_or_cross_run_inputs(self):
        self.assertEqual(WORKFLOW['on'], {'workflow_call': {}})
        self.assertEqual(JOB['permissions'], {'contents': 'read', 'packages': 'write'})
        self.assertEqual(JOB['timeout-minutes'], 10)
        self.assertIn("github.event_name == 'push'", JOB['if'])
        self.assertIn("github.ref == 'refs/heads/main'", JOB['if'])
        self.assertIn('!github.event.act', JOB['if'])
        for step in JOB['steps'] + ACTION['runs']['steps']:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^actions/(upload|download)-artifact@[0-9a-f]{40}$')
        download = JOB['steps'][1]
        self.assertEqual(download['with'], {'name': 'image-${{ github.run_id }}-${{ github.run_attempt }}'})
        self.assertEqual(STEPS['Remove registry credentials']['if'], '${{ always() }}')
        self.assertEqual(STEPS['Store candidate handoff']['with']['retention-days'], 90)
        self.assertEqual(STEPS['Publish without rebuilding']['env'], {'GHCR_TOKEN': '${{ github.token }}'})
        for step in JOB['steps']:
            self.assertNotRegex(step.get('run', ''), r'docker build|\./|secrets\.')


@unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'publisher runs on Ubuntu with Bash')
class PublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = 'ghcr.io/pipesor-org/example:sha-' + 'a' * 40 + '-123-2'
        self.reference = 'ghcr.io/pipesor-org/example@sha256:' + 'b' * 64
        self.env = {
            **os.environ, 'GITHUB_REPOSITORY': 'Pipesor-Org/Example',
            'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2',
            'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
            'ACT': '', 'ACT_EVENT': '', 'GITHUB_ENV': str(self.root / 'env'),
            'GITHUB_STEP_SUMMARY': str(self.root / 'summary'), 'IMAGE': self.image,
            'MOCK_REVISION': 'a' * 40, 'MOCK_REFERENCE': self.reference,
            'DOCKER_LOG': str(self.root / 'docker.log'),
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
        }
        docker = self.root / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
with open(os.environ['DOCKER_LOG'], 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['image', 'inspect']:
    key = 'MOCK_REVISION' if 'revision' in sys.argv[-1] else 'MOCK_REFERENCE'
    print(os.environ[key])
sys.exit(int(os.environ.get('MOCK_DOCKER_EXIT', '0')))
''')
        docker.chmod(0o755)
        archive = b'checked image archive'
        (self.root / 'service-image.tar').write_bytes(archive)
        (self.root / 'service-image.tar.sha256').write_text(
            hashlib.sha256(archive).hexdigest() + '  service-image.tar\n')

    def run_step(self, name, **env):
        return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', STEPS[name]['run']],
                              cwd=self.root, env={**self.env, **env}, capture_output=True, text=True)

    def test_guard_derives_caller_image_and_rejects_other_contexts(self):
        self.assertEqual(self.run_step('Validate publication context').returncode, 0)
        self.assertEqual((self.root / 'env').read_text(), f'IMAGE={self.image}\n')
        for override in ({'ACT': 'true'}, {'ACT_EVENT': 'true'},
                         {'GITHUB_EVENT_NAME': 'pull_request'},
                         {'GITHUB_EVENT_NAME': 'workflow_dispatch'},
                         {'GITHUB_REF': 'refs/heads/feature/test'}):
            with self.subTest(override=override):
                self.assertNotEqual(self.run_step('Validate publication context', **override).returncode, 0)

    def test_load_checks_archive_and_revision(self):
        self.assertEqual(self.run_step('Load the tested image').returncode, 0)
        self.assertNotEqual(self.run_step('Load the tested image', MOCK_REVISION='wrong').returncode, 0)
        log = (self.root / 'docker.log').read_text()
        (self.root / 'service-image.tar').write_bytes(b'corrupted')
        self.assertNotEqual(self.run_step('Load the tested image').returncode, 0)
        self.assertEqual((self.root / 'docker.log').read_text(), log)

    def test_checksum_cannot_authenticate_a_different_archive(self):
        (self.root / 'service-image.tar.sha256').write_text('0' * 64 + '  other.tar\n')
        self.assertNotEqual(self.run_step('Load the tested image').returncode, 0)
        self.assertFalse((self.root / 'docker.log').exists())

    def test_records_unchanged_candidate_schema(self):
        result = self.run_step('Record immutable candidate')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'candidate.json').read_text()), {
            'schema_version': 1, 'source_sha': 'a' * 40,
            'image': 'ghcr.io/pipesor-org/example', 'digest': 'sha256:' + 'b' * 64,
            'build_run_id': '123', 'build_run_attempt': '2',
        })

    def test_rejects_foreign_or_malformed_digest_and_docker_failure(self):
        for reference in ('ghcr.io/other/repo@sha256:' + 'b' * 64,
                          'ghcr.io/pipesor-org/example@sha256:bad', ''):
            with self.subTest(reference=reference):
                self.assertNotEqual(self.run_step('Record immutable candidate', MOCK_REFERENCE=reference).returncode, 0)
                self.assertFalse((self.root / 'candidate.json').exists())
        self.assertNotEqual(self.run_step('Record immutable candidate', IMAGE='ghcr.io/other/repo:tag').returncode, 0)
        self.assertNotEqual(self.run_step('Load the tested image', MOCK_DOCKER_EXIT='1').returncode, 0)


if __name__ == '__main__':
    unittest.main()
