"""Explicit workspace inspection and conservative synchronization; standard library only."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(path, *args):
    result = subprocess.run(args, cwd=path, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or str(args))
    return result.stdout.strip()


def git(path, *args):
    return run(path, 'git', *args)


def projects(manifest):
    # JSON is also YAML: one inventory, without a Python YAML dependency.
    data = json.loads(manifest.read_text())['projects']
    seen = set()
    for name, item in data.items():
        path = (manifest.parent / item['path']).resolve()
        if path == manifest.parent or not path.is_relative_to(manifest.parent.parent):
            raise ValueError(f'{name}: path must be a sibling repository or under services/')
        if path in seen:
            raise ValueError(f'{name}: duplicate repository path')
        seen.add(path)
        url = item['url']
        if not url.startswith('https://github.com/pipesor-org/') or not url.endswith('.git'):
            raise ValueError(f'{name}: expected a Pipesor GitHub URL')
        yield name, path, url


def ancestor(path, first, second):
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', first, second], cwd=path)
    if result.returncode not in (0, 1):
        raise RuntimeError('Unable to establish commit ancestry')
    return result.returncode == 0


def sync(path, url, apply=False):
    if not Path(git(path, 'rev-parse', '--show-toplevel')).samefile(path):
        raise RuntimeError('Path is not the root of a standalone checkout')
    if git(path, 'remote', 'get-url', 'origin') != url:
        raise RuntimeError('origin does not match the inventory')
    if git(path, 'status', '--porcelain'):
        raise RuntimeError('Uncommitted or untracked work; repository skipped')
    for marker in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'sequencer'):
        marker_path = Path(git(path, 'rev-parse', '--git-path', marker))
        if not marker_path.is_absolute():
            marker_path = path / marker_path
        if marker_path.exists():
            raise RuntimeError('Git operation in progress; repository skipped')
    if git(path, 'config', '--get-all', 'remote.origin.fetch') != '+refs/heads/*:refs/remotes/origin/*':
        raise RuntimeError('Nonstandard fetch refspec; repository skipped')
    # Preview fetches remote refs, but does not change local branches or files.
    git(path, 'fetch', '--prune', 'origin')
    target = git(path, 'rev-parse', 'refs/remotes/origin/main')
    main = git(path, 'rev-parse', 'refs/heads/main')
    if not ancestor(path, main, target):
        raise RuntimeError('Local main is ahead or diverged; repository skipped')
    active = git(path, 'symbolic-ref', '--short', 'HEAD')
    occupied = {line[7:] for line in git(path, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('branch ')}
    if active != 'main' and 'refs/heads/main' in occupied:
        raise RuntimeError('main is checked out in another worktree')
    repository = url.removeprefix('https://github.com/').removesuffix('.git')
    deletions = []
    for line in git(path, 'for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/heads').splitlines():
        branch, sha = line.split()
        if branch == 'main':
            continue
        if branch != active and 'refs/heads/' + branch in occupied:
            continue
        prs = json.loads(run(path, 'gh', 'api', '--method', 'GET', '--paginate', '--slurp',
            f'repos/{repository}/pulls', '-f', 'state=closed', '-f', 'base=main',
            '-f', f'head=pipesor-org:{branch}', '-f', 'per_page=100'))
        for pr in (pr for page in prs for pr in page):
            if (pr.get('merged_at') and pr['head']['sha'] == sha
                    and pr['head']['repo'] and pr['head']['repo']['full_name'] == repository
                    and pr['base']['repo']['full_name'] == repository
                    and pr['base']['ref'] == 'main'
                    and ancestor(path, pr['merge_commit_sha'], target)):
                deletions.append((branch, sha, pr['number']))
                break
    if active != 'main' and active not in {branch for branch, _, _ in deletions}:
        raise RuntimeError('Active branch has no verified merged PR; repository skipped')
    changed = main != target or active != 'main' or bool(deletions)
    if not changed:
        print('  Unchanged: already on main at the fetched remote commit.')
    if not apply:
        if active != 'main':
            print(f'  Would switch {active} -> main')
        if main != target:
            print(f'  Would update main: {main[:8]} -> {target[:8]} (fast-forward)')
        for branch, sha, number in deletions:
            print(f'  Would delete {branch} at {sha} (verified merged PR #{number})')
    if apply:
        if git(path, 'status', '--porcelain') or git(path, 'rev-parse', 'refs/heads/main') != main:
            raise RuntimeError('Local state changed during inspection')
        git(path, 'switch', 'main')
        if active != 'main':
            print(f'  Switched {active} -> main', flush=True)
        git(path, 'merge', '--ff-only', target)
        if main != target:
            print(f'  Updated main: {main[:8]} -> {target[:8]}', flush=True)
        for branch, sha, number in deletions:
            if 'branch refs/heads/' + branch in git(path, 'worktree', 'list', '--porcelain').splitlines():
                raise RuntimeError('Branch is now in use by a worktree; cleanup stopped')
            # Expected old SHA prevents deleting a branch changed after inspection.
            git(path, 'update-ref', '-d', 'refs/heads/' + branch, sha)
            print(f'  Deleted {branch} at {sha} (verified merged PR #{number})', flush=True)
            config = subprocess.run(['git', 'config', '--local', '--get-regexp', '^branch[.]'], cwd=path, text=True, capture_output=True)
            if config.returncode not in (0, 1):
                raise RuntimeError('Unable to inspect branch tracking configuration')
            if any(line.startswith('branch.' + branch + '.') for line in config.stdout.splitlines()):
                git(path, 'config', '--local', '--remove-section', 'branch.' + branch)
    return changed


def status(selected):
    groups = {label: [] for label in (
        'Uncommitted changes', 'Other branches (clean working tree)',
        'Main (clean working tree)', 'Unavailable')}
    for name, path, _ in selected:
        try:
            lines = git(path, 'status', '--porcelain=v1', '--branch').splitlines()
            if len(lines) > 1:
                label = 'Uncommitted changes'
            elif lines[0].split('...')[0] == '## main':
                label = 'Main (clean working tree)'
            else:
                label = 'Other branches (clean working tree)'
            groups[label].append((name, lines))
        except (RuntimeError, OSError, ValueError) as error:
            groups['Unavailable'].append((name, [str(error)]))
    print('Workspace status (local only; no fetch). Clean means no working tree changes.')
    print('Branch tracking may be stale; use sync to preview against the remote.')
    for label, entries in groups.items():
        if not entries:
            continue
        print(f'\n{label} ({len(entries)})')
        for name, lines in entries:
            print(f'  {name}')
            for line in lines:
                print(f'    {line}')
    print(f'\n{len(selected)} repositories inspected; {len(groups["Unavailable"])} unavailable.')
    return int(bool(groups['Unavailable']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'status', 'sync'])
    parser.add_argument('--apply', action='store_true', help='apply verified sync and cleanup')
    parser.add_argument('--project', help='select one inventory project')
    args = parser.parse_args()
    if args.apply and args.command != 'sync':
        parser.error('--apply is only valid for sync')
    if args.command == 'doctor':
        missing = False
        print('Workspace doctor: tool availability and GitHub access')
        print(f'\nCore tools (status/sync)\n  Python: {sys.executable}')
        for heading, tools in (
            ('', (('git', 'local status and synchronization'), ('gh', 'GitHub merge evidence'))),
            ('Additional tools', (('mani', 'clone inventory / Mani tasks'),
                                  ('bun', 'contributor checks'), ('lefthook', 'commit hooks'),
                                  ('codex', 'coding assistant')))):
            if heading:
                print(f'\n{heading}')
            for tool, purpose in tools:
                location = shutil.which(tool)
                print(f'  {tool}: {location or "MISSING"} ({purpose})')
                missing |= location is None
        print('\nGitHub access')
        for label, command in (('Authentication', ('gh', 'auth', 'status')),
                               ('API connectivity', ('gh', 'api', 'user', '--silent'))):
            try:
                run(ROOT, *command)
                print(f'  {label}: OK')
            except (RuntimeError, OSError) as error:
                print(f'  {label}: FAILED: {error}')
                missing = True
        print('\nResult: ' + ('INCOMPLETE — resolve missing tools or failed checks above.' if missing
                              else 'All tool and GitHub checks passed.'))
        print('Additional tools are not required by Python status/sync. Install tools in your active environment.')
        print('For authentication failures, run gh auth login. Repository permissions are checked during sync.')
        return int(missing)
    selected = list(projects(ROOT / 'mani.yaml'))
    if args.project:
        selected = [item for item in selected if item[0] == args.project]
        if not selected:
            parser.error('unknown project')
    if args.command == 'status':
        return status(selected)
    print('Sync apply — verified local changes will be applied.' if args.apply else
          'Sync preview — local branches and files will not change.')
    print('Fetches and prunes remote-tracking refs. Checks each repository independently.\n')
    failures = 0
    changed = 0
    unchanged = 0
    for name, path, url in selected:
        print(name, flush=True)
        try:
            if sync(path, url, args.apply):
                changed += 1
            else:
                unchanged += 1
        except (RuntimeError, OSError, ValueError) as error:
            print(f'  SKIPPED/FAILED: {error}')
            print(f'  Inspect with git -C "{path}" status; preserve work and resolve the reason before retrying.')
            if args.apply:
                print('  Apply may be partial: completed actions above remain; nothing was rolled back.')
            failures += 1
    action = 'changed' if args.apply else 'with planned changes'
    print(f'\n{len(selected)} repositories: {changed} {action}, {unchanged} unchanged, {failures} skipped/failed.')
    if not args.apply and changed:
        print('Review the plan, then rerun sync with --apply (and the same --project filter, if used).')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
