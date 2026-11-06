import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from unittest.mock import call, patch

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
        self.base = self.git('rev-parse', 'HEAD')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.git('switch', '-c', 'feature/example')
        self.commit('feature')
        self.head = self.git('rev-parse', 'HEAD')
        # Publish elsewhere so only a real fetch can discover the newer main.
        publisher = self.root / 'publisher'
        self.git('clone', '--branch', 'main', str(self.remote), str(publisher))
        self.git('config', 'user.name', 'Test', path=publisher)
        self.git('config', 'user.email', 'test@example.invalid', path=publisher)
        self.git('fetch', str(self.path), 'feature/example', path=publisher)
        self.git('merge', '--squash', 'FETCH_HEAD', path=publisher)
        self.git('commit', '-m', 'feat: integrate example', path=publisher)
        self.squash = self.git('rev-parse', 'HEAD', path=publisher)
        self.git('push', 'origin', 'main', path=publisher)
        self.git('remote', 'set-url', 'origin', self.url)
        self.pr = {'number': 1, 'merged_at': '2026-11-01', 'head': {'sha': self.head, 'repo': {'full_name': 'pipesor-org/example'}}, 'base': {'ref': 'main', 'repo': {'full_name': 'pipesor-org/example'}}, 'merge_commit_sha': self.squash}
        self.pages = [[], [self.pr]]
        self.api_branch = 'feature/example'
        original = w.run
        def runner(path, *args):
            if args[0] == 'gh':
                if 'head=pipesor-org:docs/keep' in args:
                    return json.dumps([[]])
                self.assertEqual(args, ('gh', 'api', '--method', 'GET', '--paginate', '--slurp',
                    'repos/pipesor-org/example/pulls', '-f', 'state=closed', '-f', 'base=main',
                    '-f', f'head=pipesor-org:{self.api_branch}', '-f', 'per_page=100'))
                return json.dumps(self.pages)
            if args[:3] == ('git', 'fetch', '--prune'):
                return original(path, 'git', 'fetch', '--prune', str(self.remote), '+refs/heads/*:refs/remotes/origin/*')
            return original(path, *args)
        self.mock = patch.object(w, 'run', side_effect=runner)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def git(self, *args, path=None):
        return subprocess.run(['git', *args], cwd=path or self.path, check=True, text=True, capture_output=True).stdout.strip()

    def checkout_state(self):
        files = {str(p.relative_to(self.path)): p.read_bytes() for p in self.path.rglob('*')
                 if p.is_file() and p.relative_to(self.path).parts[0] != '.git'}
        return (self.git('symbolic-ref', 'HEAD'),
                self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads'),
                self.git('diff', '--cached', '--binary'), self.git('diff', '--binary'),
                self.git('status', '--porcelain'), self.git('config', '--local', '--list'), files)

    @contextmanager
    def rejected_without_changes(self, reason):
        before = self.checkout_state()
        with self.assertRaisesRegex(RuntimeError, reason):
            yield
        self.assertEqual(self.checkout_state(), before)

    def commit(self, text):
        (self.path / text).write_text(text)
        self.git('add', text)
        self.git('commit', '-m', 'feat: '+text)

    def test_squash_cleanup_and_idempotency(self):
        self.git('config', 'branch.feature/example.remote', 'origin')
        self.git('config', 'branch.feature/example.merge', 'refs/heads/feature/example')
        self.git('branch', 'docs/keep', self.head)
        self.git('config', 'branch.docs/keep.remote', 'origin')
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(w.sync(self.path, self.url, True))
        self.assertIn('Switched feature/example -> main', output.getvalue())
        self.assertIn('Updated main:', output.getvalue())
        self.assertIn('Deleted feature/example', output.getvalue())
        self.assertNotIn('Would', output.getvalue())
        self.assertEqual(self.git('rev-parse', 'main'), self.squash)
        self.assertNotIn('feature/example', self.git('branch'))
        self.assertEqual(self.git('rev-parse', 'docs/keep'), self.head)
        config = self.git('config', '--local', '--list')
        self.assertNotIn('branch.feature/example.', config)
        self.assertIn('branch.docs/keep.remote=origin', config)
        with redirect_stdout(output):
            self.assertFalse(w.sync(self.path, self.url, True))
        self.assertIn('Unchanged:', output.getvalue())

    def test_preview_keeps_branches(self):
        before = self.checkout_state()
        self.assertEqual(self.git('rev-parse', 'origin/main'), self.base)
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(w.sync(self.path, self.url))
        self.assertIn('Would switch feature/example -> main', output.getvalue())
        self.assertIn('Would update main:', output.getvalue())
        self.assertIn('Would delete feature/example', output.getvalue())
        self.assertNotIn('Deleted', output.getvalue())
        self.assertEqual(self.checkout_state(), before)
        self.assertEqual(self.git('rev-parse', 'origin/main'), self.squash)

    def test_verified_docs_branch_switches_and_is_deleted(self):
        self.git('branch', '-m', 'docs/example')
        self.api_branch = 'docs/example'
        before = self.checkout_state()
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(w.sync(self.path, self.url))
        self.assertIn('Would switch docs/example -> main', output.getvalue())
        self.assertIn('Would delete docs/example', output.getvalue())
        self.assertEqual(self.checkout_state(), before)
        self.pr['head']['sha'] = self.base
        with self.rejected_without_changes('no verified merged PR'):
            w.sync(self.path, self.url, True)
        self.pr['head']['sha'] = self.head
        with redirect_stdout(output):
            self.assertTrue(w.sync(self.path, self.url, True))
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('rev-parse', 'main'), self.squash)
        self.assertNotIn('docs/example', self.git('branch'))

    def test_verified_inactive_docs_branch_is_deleted(self):
        self.git('branch', '-m', 'docs/example')
        self.git('switch', 'main')
        self.api_branch = 'docs/example'
        with redirect_stdout(StringIO()):
            self.assertTrue(w.sync(self.path, self.url, True))
        self.assertNotIn('docs/example', self.git('branch'))

    def test_apply_failure_reports_completed_actions(self):
        original = w.git
        newer = None
        def fail_deletion(path, *args):
            nonlocal newer
            if args[0] == 'update-ref':
                # Advance the candidate after inspection; Git must reject its stale SHA.
                self.git('switch', 'feature/example')
                self.commit('concurrent-work')
                newer = self.git('rev-parse', 'HEAD')
                self.git('switch', 'main')
            return original(path, *args)
        output = StringIO()
        with patch.object(w, 'git', side_effect=fail_deletion), \
                patch.object(w, 'projects', return_value=[('example', self.path, self.url)]), \
                patch.object(w.sys, 'argv', ['workspace.py', 'sync', '--apply']), \
                redirect_stdout(output):
            self.assertEqual(w.main(), 1)
        report = output.getvalue()
        self.assertIn('Sync apply', report)
        self.assertIn('Updated main:', report)
        self.assertNotIn('Deleted feature/example', report)
        self.assertIn('Apply may be partial', report)
        self.assertIn('0 changed, 0 unchanged, 1 skipped/failed', report)
        self.assertEqual(self.git('rev-parse', 'main'), self.squash)
        self.assertEqual(self.git('rev-parse', 'feature/example'), newer)

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
        with self.rejected_without_changes('no verified merged PR'):
            w.sync(self.path, self.url, True)

    def test_closed_without_merge_is_preserved(self):
        self.pr['merged_at'] = None
        with self.rejected_without_changes('no verified merged PR'):
            w.sync(self.path, self.url, True)

    def test_dirty_checkout_is_preserved(self):
        for kind in ('untracked', 'staged', 'unstaged'):
            with self.subTest(kind=kind):
                file = self.path / ('untracked' if kind == 'untracked' else 'base')
                file.write_text('precious ' + kind)
                if kind == 'staged':
                    self.git('add', 'base')
                with self.rejected_without_changes('Uncommitted'):
                    w.sync(self.path, self.url, True)
                if kind == 'untracked':
                    file.unlink()
                else:
                    self.git('reset', '--hard', 'HEAD')

    def test_diverged_main_is_preserved(self):
        self.git('switch', 'main')
        self.commit('local')
        with self.rejected_without_changes('ahead or diverged'):
            w.sync(self.path, self.url, True)

    def test_branch_in_another_worktree_is_preserved(self):
        self.git('switch', 'main')
        self.git('worktree', 'add', str(self.root/'worktree'), 'feature/example')
        w.sync(self.path, self.url, True)
        self.assertIn('feature/example', self.git('branch'))

    def test_missing_squash_on_main_is_preserved(self):
        self.pr['merge_commit_sha'] = self.head
        with self.rejected_without_changes('no verified merged PR'):
            w.sync(self.path, self.url, True)

    def test_wrong_remote_is_preserved(self):
        with self.rejected_without_changes('origin does not match'):
            w.sync(self.path, 'https://github.com/pipesor-org/other.git', True)

    def test_nonstandard_refspec_is_preserved(self):
        self.git('config', 'remote.origin.fetch', '+refs/tags/*:refs/tags/*')
        with self.rejected_without_changes('Nonstandard fetch refspec'):
            w.sync(self.path, self.url, True)

    def test_api_failure_keeps_branches(self):
        original = w.run
        def unavailable(path, *args):
            if args[0] == 'gh':
                raise RuntimeError('GitHub unavailable')
            return original(path, *args)
        with patch.object(w, 'run', side_effect=unavailable):
            with self.rejected_without_changes('GitHub unavailable'):
                w.sync(self.path, self.url, True)
        self.assertEqual(self.git('branch', '--show-current'), 'feature/example')

    def test_wrong_pr_identity_keeps_checkout(self):
        original = copy.deepcopy(self.pr)
        for field, value in (('head_repo', 'other/repo'), ('base_repo', 'other/repo'),
                             ('base_ref', 'release'), ('head_repo', None), ('empty', None)):
            with self.subTest(field=field, value=value):
                self.pr.clear()
                self.pr.update(copy.deepcopy(original))
                self.pages = [[], [self.pr]]
                if field == 'empty':
                    self.pages = [[]]
                elif field == 'base_ref':
                    self.pr['base']['ref'] = value
                else:
                    self.pr[field.split('_')[0]]['repo'] = {'full_name': value} if value else None
                with self.rejected_without_changes('no verified merged PR'):
                    w.sync(self.path, self.url, True)

    def test_fetch_failure_keeps_checkout(self):
        original = w.run
        def unavailable(path, *args):
            if args[:2] == ('git', 'fetch'):
                raise RuntimeError('fetch unavailable')
            return original(path, *args)
        with patch.object(w, 'run', side_effect=unavailable):
            with self.rejected_without_changes('fetch unavailable'):
                w.sync(self.path, self.url, True)

    def test_pending_operation_keeps_checkout(self):
        # A merge can be pending even when its index and working tree are clean.
        marker = self.path / '.git' / 'MERGE_HEAD'
        marker.write_text(self.head + '\n')
        with self.rejected_without_changes('Git operation in progress'):
            w.sync(self.path, self.url, True)
        self.assertEqual(marker.read_text(), self.head + '\n')

    def test_changes_during_inspection_are_preserved(self):
        original = w.run
        for change in ('working_tree', 'main'):
            with self.subTest(change=change):
                after_change = None
                def race(path, *args):
                    nonlocal after_change
                    result = original(path, *args)
                    if args[0] == 'gh':
                        if change == 'working_tree':
                            (self.path / 'concurrent-work').write_text('keep this')
                        else:
                            self.git('update-ref', 'refs/heads/main', self.head)
                        after_change = self.checkout_state()
                    return result
                with patch.object(w, 'run', side_effect=race):
                    with self.assertRaisesRegex(RuntimeError, 'Local state changed during inspection'):
                        w.sync(self.path, self.url, True)
                self.assertEqual(self.checkout_state(), after_change)
                if change == 'working_tree':
                    (self.path / 'concurrent-work').unlink()

    def test_new_worktree_during_apply_keeps_branch(self):
        original = w.git
        worktree = self.root / 'new-worktree'
        def race(path, *args):
            result = original(path, *args)
            if args[0] == 'merge':
                self.git('worktree', 'add', str(worktree), 'feature/example')
            return result
        with patch.object(w, 'git', side_effect=race):
            with self.assertRaisesRegex(RuntimeError, 'Branch is now in use'):
                w.sync(self.path, self.url, True)
        self.assertEqual(self.git('rev-parse', 'feature/example'), self.head)
        self.assertEqual(self.git('rev-parse', 'HEAD', path=worktree), self.head)
        self.assertEqual(self.git('rev-parse', 'main'), self.squash)

    def test_main_change_before_merge_is_not_merged_or_overwritten(self):
        original = w.git
        after_change = None
        def race(path, *args):
            nonlocal after_change
            if args[0] == 'merge':
                self.commit('concurrent-main')
                after_change = self.checkout_state()
            return original(path, *args)
        with patch.object(w, 'git', side_effect=race):
            with self.assertRaises(RuntimeError):
                w.sync(self.path, self.url, True)
        self.assertIsNotNone(after_change)
        self.assertEqual(self.checkout_state(), after_change)


class SyncReportTests(unittest.TestCase):
    def test_preview_counts_and_continues_after_failure(self):
        selected = [(name, Path(name), '') for name in ('update', 'missing', 'unchanged')]
        output = StringIO()
        with patch.object(w.sys, 'argv', ['workspace.py', 'sync']), \
                patch.object(w, 'projects', return_value=selected), \
                patch.object(w, 'sync', side_effect=[True, OSError('checkout missing'), False]) as sync, \
                redirect_stdout(output):
            self.assertEqual(w.main(), 1)
        report = output.getvalue()
        for text in ('Sync preview', 'Fetches and prunes remote-tracking refs',
                     '3 repositories: 1 with planned changes, 1 unchanged, 1 skipped/failed',
                     'checkout missing', 'preserve work', 'rerun sync with --apply'):
            self.assertIn(text, report)
        self.assertEqual(sync.call_count, 3)
        self.assertNotIn('Apply may be partial', report)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'workspace'
        self.root.mkdir()
        self.manifest = self.root / 'mani.yaml'

    def write_manifest(self, items):
        self.manifest.write_text(json.dumps({'projects': items}))

    def test_loads_sibling_and_service_and_checked_in_inventory(self):
        url = 'https://github.com/pipesor-org/example.git'
        self.write_manifest({'example': {'path': '../example', 'url': url},
                             'service': {'path': '../services/service', 'url': url}})
        self.assertEqual(list(w.projects(self.manifest)), [
            ('example', (self.root.parent / 'example').resolve(), url),
            ('service', (self.root.parent / 'services/service').resolve(), url)])
        self.assertTrue(list(w.projects(w.ROOT / 'mani.yaml')))

    def test_rejects_unsafe_or_ambiguous_inventory(self):
        url = 'https://github.com/pipesor-org/example.git'
        for path, other_path, origin, reason in (
            ('.', None, url, 'path must be'),
            ('../../outside', None, url, 'path must be'),
            ('../example', '../example/../example', url, 'duplicate'),
            ('../example', None, 'https://github.com/other/example.git', 'Pipesor GitHub URL'),
        ):
            with self.subTest(path=path, other_path=other_path, origin=origin):
                items = {'example': {'path': path, 'url': origin}}
                if other_path:
                    items['duplicate'] = {'path': other_path, 'url': url}
                self.write_manifest(items)
                with self.assertRaisesRegex(ValueError, reason):
                    list(w.projects(self.manifest))

    def test_cli_selects_only_requested_project(self):
        url = 'https://github.com/pipesor-org/example.git'
        self.write_manifest({name: {'path': '../' + name, 'url': url}
                             for name in ('untouched', 'example')})
        expected = (self.root.parent / 'example').resolve()
        for command in ('status', 'sync'):
            with self.subTest(command=command), \
                    patch.object(w, 'ROOT', self.root), \
                    patch.object(w.sys, 'argv', ['workspace.py', command, '--project', 'example'] +
                                 (['--apply'] if command == 'sync' else [])), \
                    patch.object(w, 'git', return_value='## HEAD (no branch)') as git, \
                    patch.object(w, 'sync', return_value=False) as sync, redirect_stdout(StringIO()):
                self.assertEqual(w.main(), 0)
                if command == 'status':
                    git.assert_called_once_with(expected, 'status', '--porcelain=v1', '--branch')
                    sync.assert_not_called()
                else:
                    sync.assert_called_once_with(expected, url, True)
                    git.assert_not_called()

    def test_invalid_cli_cannot_start_work(self):
        self.write_manifest({'example': {'path': '../example',
                            'url': 'https://github.com/pipesor-org/example.git'}})
        for arguments in (['sync', '--project', 'unknown'], ['status', '--apply'], ['doctor', '--apply']):
            with self.subTest(arguments=arguments), patch.object(w, 'ROOT', self.root), \
                    patch.object(w.sys, 'argv', ['workspace.py', *arguments]), \
                    patch.object(w, 'git') as git, patch.object(w, 'sync') as sync, \
                    redirect_stdout(StringIO()), patch('sys.stderr', new=StringIO()):
                with self.assertRaises(SystemExit) as error:
                    w.main()
                self.assertEqual(error.exception.code, 2)
                git.assert_not_called()
                sync.assert_not_called()


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
        for detail in ('local only; no fetch', '4 repositories inspected; 1 unavailable'):
            self.assertIn(detail, report)
        self.assertEqual(git.call_count, 4)
        for index, (heading, name, details) in enumerate((
            (headings[0], 'dirty', '## feature/docs\n     M tracked.md\n    ?? new.md'),
            (headings[1], 'feature', '## feature/example'),
            (headings[2], 'main', '## main...origin/main [behind 2]'),
            (headings[3], 'missing', 'checkout missing'),
        )):
            section = report.split(heading, 1)[1]
            if index + 1 < len(headings):
                section = section.split(headings[index + 1], 1)[0]
            self.assertIn(f'  {name}\n    {details}', section)


class DoctorTests(unittest.TestCase):
    def test_missing_core_or_additional_tool_makes_doctor_incomplete(self):
        for tool in ('git', 'bun'):
            output = StringIO()
            with self.subTest(tool=tool), patch.object(w.sys, 'argv', ['workspace.py', 'doctor']), \
                    patch.object(w.shutil, 'which', side_effect=lambda name: None if name == tool else '/tool'), \
                    patch.object(w, 'run', return_value=''), redirect_stdout(output):
                self.assertEqual(w.main(), 1)
            report = output.getvalue()
            for text in ('Core tools', 'Additional tools', f'{tool}: MISSING',
                         'Authentication: OK', 'API connectivity: OK', 'Result: INCOMPLETE',
                         'Additional tools are not required by Python status/sync'):
                self.assertIn(text, report)

    def test_success(self):
        output = StringIO()
        with patch.object(w.sys, 'argv', ['workspace.py', 'doctor']), \
                patch.object(w.shutil, 'which', return_value='/tool'), \
                patch.object(w, 'run', return_value='') as run, redirect_stdout(output):
            self.assertEqual(w.main(), 0)
        self.assertIn('All tool and GitHub checks passed', output.getvalue())
        self.assertEqual(run.call_args_list, [call(w.ROOT, 'gh', 'auth', 'status'),
                                             call(w.ROOT, 'gh', 'api', 'user', '--silent')])

    def test_github_failures_are_reported_independently(self):
        for command, failed, passed in (('auth', 'Authentication', 'API connectivity'),
                                        ('api', 'API connectivity', 'Authentication')):
            def fake_run(path, *args):
                if args[:2] == ('gh', command):
                    raise RuntimeError('access unavailable')
                return ''
            output = StringIO()
            with self.subTest(command=command), \
                    patch.object(w.sys, 'argv', ['workspace.py', 'doctor']), \
                    patch.object(w.shutil, 'which', return_value='/tool'), \
                    patch.object(w, 'run', side_effect=fake_run), redirect_stdout(output):
                self.assertEqual(w.main(), 1)
            self.assertIn(f'{failed}: FAILED: access unavailable', output.getvalue())
            self.assertNotIn(f'{failed}: OK', output.getvalue())
            self.assertIn(f'{passed}: OK', output.getvalue())
