"""Explicit workspace inspection and conservative synchronization; standard library only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_URL = 'https://github.com/pipesor-org/pipesor-workspace.git'


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


def worktrees(path):
    """Parse `git worktree list --porcelain`; the first entry is the primary checkout."""
    items = []
    for line in git(path, 'worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            items.append({'path': Path(line[9:]), 'branch': None, 'locked': False})
        elif line.startswith('branch refs/heads/'):
            items[-1]['branch'] = line[18:]
        elif line == 'locked' or line.startswith('locked '):
            items[-1]['locked'] = True
    return items


def blocker(path):
    """Return why a checkout must not be touched, or None when it is clean and idle."""
    if not path.is_dir():
        return 'worktree directory is missing (see git worktree prune)'
    if git(path, 'status', '--porcelain'):
        return 'uncommitted or untracked work'
    for marker in ('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'sequencer'):
        marker_path = Path(git(path, 'rev-parse', '--git-path', marker))
        if not marker_path.is_absolute():
            marker_path = path / marker_path
        if marker_path.exists():
            return 'Git operation in progress'
    return None


def pull_requests(path, repository, branch):
    pages = json.loads(run(path, 'gh', 'api', '--method', 'GET', '--paginate', '--slurp',
        f'repos/{repository}/pulls', '-f', 'state=all', '-f', 'base=main',
        '-f', f'head=pipesor-org:{branch}', '-f', 'per_page=100'))
    return [pr for page in pages for pr in page]


def merged_pr(path, repository, sha, target, prs):
    for pr in prs:
        if (pr.get('merged_at') and pr['head']['sha'] == sha
                and pr['head']['repo'] and pr['head']['repo']['full_name'] == repository
                and pr['base']['repo']['full_name'] == repository
                and pr['base']['ref'] == 'main'
                and ancestor(path, pr['merge_commit_sha'], target)):
            return pr['number']
    return None


def kept_detail(path, branch, sha, target, prs):
    """Explain why a branch without verified merge evidence stays, so it can be acted on."""
    facts = []
    open_prs = [pr['number'] for pr in prs if pr.get('state') == 'open']
    merged = [pr['number'] for pr in prs if pr.get('merged_at')]
    if open_prs:
        facts.append('open PR ' + ', '.join(f'#{number}' for number in open_prs))
    elif merged:
        facts.append('merged PR ' + ', '.join(f'#{number}' for number in merged)
                     + ' has a different head or its squash is not on main')
    elif prs:
        facts.append('PR closed without merge')
    else:
        facts.append('no PR')
    ahead = int(git(path, 'rev-list', '--count', f'{target}..{sha}'))
    facts.append(f'{ahead} commit{"" if ahead == 1 else "s"} not in origin/main')
    remote = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}'],
                            cwd=path, text=True, capture_output=True)
    if remote.returncode:
        facts.append('local only')
    elif remote.stdout.strip() == sha:
        facts.append('same as origin')
    else:
        facts.append('differs from origin')
    return '; '.join(facts)


def occupants(path):
    """Other processes whose working directory is inside path (Linux /proc only; else none)."""
    proc = Path('/proc')
    if not proc.is_dir():
        return []
    own, pid = set(), os.getpid()
    while pid > 1 and pid not in own:  # Skip this script and the shell that started it.
        own.add(pid)
        try:
            pid = int((proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in own:
            continue
        try:
            cwd = Path(os.readlink(entry / 'cwd'))
            name = (entry / 'comm').read_text().strip()
        except OSError:
            continue
        if cwd == path or path in cwd.parents:
            found.append(f'{name} ({entry.name})')
    return sorted(found)


def sync(path, url, apply=False, prune_worktrees=False, out=None):
    """Plan, and optionally apply, each step independently: a blocked step is reported
    as kept with its reason and never prevents the safe steps of the same repository.
    Lines go to out (default print); an unchanged repository with nothing kept is silent."""
    out = out or (lambda line: print(line, flush=True))
    path = Path(path)
    common = git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    own = git(path, 'rev-parse', '--path-format=absolute', '--git-dir')
    if not Path(git(path, 'rev-parse', '--show-toplevel')).samefile(path) or not Path(common).samefile(own):
        raise RuntimeError('Path is not the root of a standalone checkout')
    if git(path, 'remote', 'get-url', 'origin') != url:
        raise RuntimeError('origin does not match the inventory')
    if git(path, 'config', '--get-all', 'remote.origin.fetch') != '+refs/heads/*:refs/remotes/origin/*':
        raise RuntimeError('Nonstandard fetch refspec; repository skipped')
    # Preview fetches remote refs, but does not change local branches or files.
    git(path, 'fetch', '--prune', 'origin')
    target = git(path, 'rev-parse', 'refs/remotes/origin/main')
    main = git(path, 'rev-parse', 'refs/heads/main')
    if not ancestor(path, main, target):
        raise RuntimeError('Local main is ahead or diverged; repository skipped')
    trees = worktrees(path)
    primary = trees[0]
    holders = {tree['branch']: tree for tree in trees if tree['branch']}
    primary_state = git(path, 'status', '--porcelain')
    primary_blocker = blocker(path)
    repository = url.removeprefix('https://github.com/').removesuffix('.git')
    switch, removals, deletions, kept = None, [], [], []
    for line in git(path, 'for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/heads').splitlines():
        branch, sha = line.split()
        if branch == 'main':
            continue
        prs = pull_requests(path, repository, branch)
        number = merged_pr(path, repository, sha, target, prs)
        holder = holders.get(branch)
        if number is None:
            where = '' if holder is None else f' (checked out in {holder["path"]})'
            kept.append(f'Kept {branch}{where}: no verified merged PR '
                        f'({kept_detail(path, branch, sha, target, prs)})')
            continue
        reason = None
        if holder is primary:
            reason = primary_blocker
            if not reason and 'main' in holders:
                reason = f'main is checked out in {holders["main"]["path"]}'
        elif holder is not None:
            if holder['locked']:
                reason = f'worktree {holder["path"]} is locked'
            elif not prune_worktrees:
                reason = f'checked out in worktree {holder["path"]}; rerun with --prune-worktrees to remove it'
            else:
                reason = blocker(holder['path'])
                if reason:
                    reason = f'worktree {holder["path"]} has {reason}'
        if reason:
            kept.append(f'Kept {branch} (verified merged PR #{number}): {reason}')
            continue
        if holder is primary:
            switch = branch
        elif holder is not None:
            removals.append((holder['path'], branch, sha, number))
        deletions.append((branch, sha, number))
    update_main = False
    if main != target:
        holder = None if switch else holders.get('main')
        reason = holder and blocker(holder['path'])
        if reason:
            kept.append(f'Kept main at {main[:8]} behind {target[:8]}: {holder["path"]} has {reason}')
        else:
            update_main = True
    changed = bool(switch or update_main or deletions)
    touched = [tree for tree, *_ in removals]
    if switch:
        touched.append(primary['path'])
    if update_main and not switch and holders.get('main'):
        touched.append(holders['main']['path'])
    for tree in touched:
        found = occupants(tree.resolve())
        if found:
            out(f'  Warning: {", ".join(found)} working in {tree}; '
                'close or move them if a branch switch or removal would surprise them')
    if not apply:
        if switch:
            out(f'  Would switch {switch} -> main')
        if update_main:
            out(f'  Would update main: {main[:8]} -> {target[:8]} (fast-forward)')
        for tree, branch, sha, number in removals:
            out(f'  Would remove worktree {tree} ({branch}, verified merged PR #{number})')
        for branch, sha, number in deletions:
            out(f'  Would delete {branch} at {sha} (verified merged PR #{number})')
    for note in kept:
        out('  ' + note)
    if not apply:
        return changed
    if git(path, 'status', '--porcelain') != primary_state or git(path, 'rev-parse', 'refs/heads/main') != main:
        raise RuntimeError('Local state changed during inspection')
    if switch:
        git(path, 'switch', 'main')
        out(f'  Switched {switch} -> main')
    if update_main:
        holder = {tree['branch']: tree for tree in worktrees(path)}.get('main')
        if holder is None:
            # Not checked out anywhere: move the ref only if it still has the inspected value.
            git(path, 'update-ref', 'refs/heads/main', target, main)
        else:
            if blocker(holder['path']):
                raise RuntimeError(f'{holder["path"]} changed during inspection; main not updated')
            git(holder['path'], 'merge', '--ff-only', target)
        out(f'  Updated main: {main[:8]} -> {target[:8]}')
    for tree, branch, sha, number in removals:
        current = next((item for item in worktrees(path) if item['path'] == tree), None)
        if (current is None or current['branch'] != branch or current['locked']
                or blocker(tree) or git(tree, 'rev-parse', 'HEAD') != sha):
            raise RuntimeError(f'Worktree {tree} changed during inspection; cleanup stopped')
        # Without --force, Git itself refuses dirty or locked worktrees; ignored files go.
        git(path, 'worktree', 'remove', str(tree))
        out(f'  Removed worktree {tree} ({branch}, verified merged PR #{number})')
    for branch, sha, number in deletions:
        if 'branch refs/heads/' + branch in git(path, 'worktree', 'list', '--porcelain').splitlines():
            raise RuntimeError('Branch is now in use by a worktree; cleanup stopped')
        # Expected old SHA prevents deleting a branch changed after inspection.
        git(path, 'update-ref', '-d', 'refs/heads/' + branch, sha)
        out(f'  Deleted {branch} at {sha} (verified merged PR #{number})')
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
    parser.add_argument('--prune-worktrees', action='store_true',
                        help='also remove clean linked worktrees whose branch has a verified merged PR')
    parser.add_argument('--include-workspace', action='store_true',
                        help='also sync this pipesor-workspace checkout, after the inventory')
    args = parser.parse_args()
    if args.apply and args.command != 'sync':
        parser.error('--apply is only valid for sync')
    for flag in ('prune_worktrees', 'include_workspace'):
        if getattr(args, flag) and args.command != 'sync':
            parser.error(f'--{flag.replace("_", "-")} is only valid for sync')
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
    if args.include_workspace:
        # Last, so the inventory is processed by the code that started this run.
        selected.append(('pipesor-workspace', ROOT, WORKSPACE_URL))
    if args.project:
        selected = [item for item in selected if item[0] == args.project]
        if not selected:
            parser.error('unknown project')
    if args.command == 'status':
        return status(selected)
    print('Sync apply — verified local changes will be applied.' if args.apply else
          'Sync preview — local branches and files will not change.')
    print('Fetches and prunes remote-tracking refs. Checks each repository independently.')
    print('Linked worktrees of merged branches: ' + ('removed when clean.\n' if args.prune_worktrees else
          'kept and reported (use --prune-worktrees to remove clean ones).\n'))
    failures = 0
    changed = 0
    quiet = []
    for name, path, url in selected:
        header = [name]
        def out(line):
            if header:
                print(header.pop(), flush=True)
            print(line, flush=True)
        try:
            if sync(path, url, args.apply, args.prune_worktrees, out):
                changed += 1
            elif header:
                quiet.append(name)
            else:
                out('  Unchanged: main matches the fetched remote commit; nothing to clean.')
        except (RuntimeError, OSError, ValueError) as error:
            out(f'  SKIPPED/FAILED: {error}')
            out(f'  Inspect with git -C "{path}" status; preserve work and resolve the reason before retrying.')
            if args.apply:
                out('  Apply may be partial: completed actions above remain; nothing was rolled back.')
            failures += 1
    unchanged = len(selected) - changed - failures
    if quiet:
        print(f'\nUp to date, nothing kept ({len(quiet)}): ' + ', '.join(quiet))
    action = 'changed' if args.apply else 'with planned changes'
    print(f'\n{len(selected)} repositories: {changed} {action}, {unchanged} unchanged, {failures} skipped/failed.')
    if not args.apply and changed:
        print('Review the plan, then rerun sync with --apply (and the same --project, --prune-worktrees\n'
              'and --include-workspace options, if used).')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
