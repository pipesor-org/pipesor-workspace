import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('workspace', Path(__file__).parents[1] / 'tools/workspace.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'repo'
        self.path.mkdir()
        self.remote = self.root / 'origin.git'
        self.url = 'https://github.com/pipesor-org/example.git'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.commit('base')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.git('switch', '-c', 'feature/example')
        self.commit('feature')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('switch', 'main')
        self.git('merge', '--squash', 'feature/example')
        self.git('commit', '-m', 'feat: integrate example')
        self.squash = self.git('rev-parse', 'HEAD')
        self.git('push', 'origin', 'main')
        self.git('reset', '--hard', 'HEAD~1')
        self.git('switch', 'feature/example')
        self.git('remote', 'set-url', 'origin', self.url)
        self.pr = {'number': 1, 'merged_at': '2026-11-01', 'head': {'sha': self.head, 'repo': {'full_name': 'pipesor-org/example'}}, 'base': {'ref': 'main', 'repo': {'full_name': 'pipesor-org/example'}}, 'merge_commit_sha': self.squash}
        original = w.run
        def runner(path, *args):
            if args[0] == 'gh':
                return json.dumps([[self.pr]])
            if args[:3] == ('git', 'fetch', '--prune'):
                return original(path, 'git', 'fetch', '--prune', str(self.remote), '+refs/heads/*:refs/remotes/origin/*')
            return original(path, *args)
        self.mock = patch.object(w, 'run', side_effect=runner)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.path, check=True, text=True, capture_output=True).stdout.strip()

    def commit(self, text):
        (self.path / text).write_text(text)
        self.git('add', text)
        self.git('commit', '-m', 'feat: '+text)

    def test_squash_cleanup_and_idempotency(self):
        w.sync(self.path, self.url, True)
        self.assertEqual(self.git('rev-parse', 'main'), self.squash)
        self.assertNotIn('feature/example', self.git('branch'))
        w.sync(self.path, self.url, True)

    def test_preview_keeps_branches(self):
        before = self.git('rev-parse', 'main')
        w.sync(self.path, self.url)
        self.assertEqual(self.git('rev-parse', 'main'), before)
        self.assertEqual(self.git('branch', '--show-current'), 'feature/example')

    def test_equivalent_path_is_accepted(self):
        w.sync(self.path / '..' / 'repo', self.url)
        self.assertEqual(self.git('branch', '--show-current'), 'feature/example')

    def test_subdirectory_is_rejected(self):
        subdirectory = self.path / 'nested'
        subdirectory.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'root of a standalone checkout'):
            w.sync(subdirectory, self.url)

    def test_unpushed_commit_keeps_active_branch(self):
        self.commit('extra')
        with self.assertRaisesRegex(RuntimeError, 'no verified merged PR'):
            w.sync(self.path, self.url, True)
        self.assertEqual(self.git('rev-parse', 'feature/example'), self.git('rev-parse', 'HEAD'))

    def test_closed_without_merge_is_preserved(self):
        self.pr['merged_at'] = None
        with self.assertRaises(RuntimeError):
            w.sync(self.path, self.url, True)

    def test_dirty_checkout_is_preserved(self):
        (self.path/'untracked').write_text('work')
        with self.assertRaisesRegex(RuntimeError, 'Uncommitted'):
            w.sync(self.path, self.url, True)

    def test_diverged_main_is_preserved(self):
        self.git('switch', 'main')
        self.commit('local')
        with self.assertRaisesRegex(RuntimeError, 'ahead or diverged'):
            w.sync(self.path, self.url, True)

    def test_branch_in_another_worktree_is_preserved(self):
        self.git('switch', 'main')
        self.git('worktree', 'add', str(self.root/'worktree'), 'feature/example')
        w.sync(self.path, self.url, True)
        self.assertIn('feature/example', self.git('branch'))

    def test_missing_squash_on_main_is_preserved(self):
        self.pr['merge_commit_sha'] = self.head
        with self.assertRaises(RuntimeError):
            w.sync(self.path, self.url, True)

    def test_wrong_remote_is_preserved(self):
        with self.assertRaisesRegex(RuntimeError, 'origin does not match'):
            w.sync(self.path, 'https://github.com/pipesor-org/other.git', True)

    def test_nonstandard_refspec_is_preserved(self):
        self.git('config', 'remote.origin.fetch', '+refs/tags/*:refs/tags/*')
        with self.assertRaisesRegex(RuntimeError, 'Nonstandard fetch refspec'):
            w.sync(self.path, self.url, True)

    def test_api_failure_keeps_branches(self):
        original = w.run
        def unavailable(path, *args):
            if args[0] == 'gh':
                raise RuntimeError('GitHub unavailable')
            return original(path, *args)
        with patch.object(w, 'run', side_effect=unavailable):
            with self.assertRaisesRegex(RuntimeError, 'GitHub unavailable'):
                w.sync(self.path, self.url, True)
        self.assertEqual(self.git('branch', '--show-current'), 'feature/example')


class StatusTests(unittest.TestCase):
    def test_groups_changes_branches_main_and_failures(self):
        selected = [(name, Path(name), '') for name in ('main', 'feature', 'dirty', 'missing')]
        output = StringIO()
        with patch.object(w, 'git', side_effect=[
            '## main...origin/main [behind 2]',
            '## feature/example',
            '## feature/docs\n M tracked.md\n?? new.md',
            OSError('checkout missing'),
        ]) as git, redirect_stdout(output):
            self.assertEqual(w.status(selected), 1)
        report = output.getvalue()
        headings = ['Uncommitted changes (1)', 'Other branches (clean working tree) (1)',
                    'Main (clean working tree) (1)', 'Unavailable (1)']
        self.assertEqual([report.index(label) for label in headings],
                         sorted(report.index(label) for label in headings))
        for detail in (' M tracked.md', '?? new.md', '[behind 2]', 'checkout missing',
                       'local only; no fetch', '4 repositories inspected; 1 unavailable'):
            self.assertIn(detail, report)
        self.assertEqual(git.call_count, 4)

    def test_project_filter_and_success(self):
        output = StringIO()
        selected = [('example', Path('example'), '')]
        with patch.object(w.sys, 'argv', ['workspace.py', 'status', '--project', 'example']), \
                patch.object(w, 'projects', return_value=selected), \
                patch.object(w, 'git', return_value='## HEAD (no branch)') as git, \
                redirect_stdout(output):
            self.assertEqual(w.main(), 0)
        git.assert_called_once_with(Path('example'), 'status', '--porcelain=v1', '--branch')
        self.assertIn('Other branches (clean working tree) (1)', output.getvalue())
        self.assertNotIn('Unavailable (', output.getvalue())


class DoctorTests(unittest.TestCase):
    def test_github_api_failure_makes_doctor_fail(self):
        from contextlib import redirect_stdout
        from io import StringIO

        def fake_run(path, *args):
            if args[:2] == ('gh', 'api'):
                raise RuntimeError('network blocked')
            return ''

        output = StringIO()
        with patch.object(w.sys, 'argv', ['workspace.py', 'doctor']), patch.object(w.shutil, 'which', return_value='/bin/tool'), patch.object(w, 'run', side_effect=fake_run), redirect_stdout(output):
            self.assertEqual(w.main(), 1)
        self.assertIn('network blocked', output.getvalue())
