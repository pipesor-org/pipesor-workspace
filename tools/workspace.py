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
    if Path(git(path, 'rev-parse', '--show-toplevel')).resolve() != path:
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
        if not branch.startswith(('feature/', 'fix/')):
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
    print(f'  main: {main[:8]} -> {target[:8]}')
    for branch, sha, number in deletions:
        print(f'  delete {branch} at {sha} (merged PR #{number})')
    if apply:
        if git(path, 'status', '--porcelain') or git(path, 'rev-parse', 'refs/heads/main') != main:
            raise RuntimeError('Local state changed during inspection')
        git(path, 'switch', 'main')
        git(path, 'merge', '--ff-only', target)
        for branch, sha, _ in deletions:
            if 'branch refs/heads/' + branch in git(path, 'worktree', 'list', '--porcelain').splitlines():
                raise RuntimeError('Branch is now in use by a worktree; cleanup stopped')
            # Expected old SHA prevents deleting a branch changed after inspection.
            git(path, 'update-ref', '-d', 'refs/heads/' + branch, sha)
            config = subprocess.run(['git', 'config', '--local', '--get-regexp', '^branch[.]'], cwd=path, text=True, capture_output=True)
            if config.returncode not in (0, 1):
                raise RuntimeError('Unable to inspect branch tracking configuration')
            if any(line.startswith('branch.' + branch + '.') for line in config.stdout.splitlines()):
                git(path, 'config', '--local', '--remove-section', 'branch.' + branch)
    return target


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
        for tool in ('git', 'gh', 'mani', 'python3', 'bun', 'lefthook', 'codex'):
            location = shutil.which(tool)
            print(f'{tool}: {location or "MISSING"}')
            missing |= location is None
        try:
            run(ROOT, 'gh', 'auth', 'status')
            print('GitHub authentication: OK (repository permissions checked during sync)')
            run(ROOT, 'gh', 'api', 'user', '--silent')
            print('GitHub API connectivity: OK')
        except (RuntimeError, OSError) as error:
            print(f'GitHub authentication or API connectivity: {error}')
            missing = True
        return int(missing)
    selected = list(projects(ROOT / 'mani.yaml'))
    if args.project:
        selected = [item for item in selected if item[0] == args.project]
        if not selected:
            parser.error('unknown project')
    failures = 0
    for name, path, url in selected:
        print(name, flush=True)
        try:
            if args.command == 'status':
                print(git(path, 'status', '--short', '--branch'))
            else:
                sync(path, url, args.apply)
        except (RuntimeError, OSError, ValueError) as error:
            print(f'  SKIPPED: {error}')
            failures += 1
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
