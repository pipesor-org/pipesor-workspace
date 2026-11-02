# Pipesor workspace

Development tooling for sibling Pipesor repositories. Each repository keeps its own
Git history, reviews, hooks and releases. This workspace updates local checkouts;
it does not deploy environments or establish a compatible release across services.

## Onboarding

Place this checkout beside `pipesor-frontend`, `pipesor-iac`, `pipesor-landing`,
`.github`, and `services/`. Install Python 3.10+, Git, GitHub CLI, and
[Mani 0.32.1](https://github.com/alajmo/mani/releases/tag/v0.32.1).
Authenticate with `gh auth login` using an account with access to the private repos.

```sh
cd pipesor-workspace
python3 tools/workspace.py doctor
mani sync
python3 tools/workspace.py status
```

`mani sync` clones missing repositories. Review the manifest before cloning.
It does not update existing main branches. `mani.yaml` uses JSON syntax, which is
valid YAML, so the Python utility reads the same inventory without extra packages.
[Mani configuration](https://manicli.com/config/) defines paths relative to this file.
The checked-in VS Code workspace opens the sibling repositories and `services/`.

For contributing to this repository, install Bun 1.4.4 and standalone Lefthook
2.2.2, verifying the binary against the official release checksums, then run:

```sh
bun install --frozen-lockfile
bun run setup:hooks
bun run test
```

Doctor reports executable availability, GitHub authentication and GitHub API connectivity. It does not
install software, change global configuration or prove access to every repo.
Bun and Lefthook are needed for contributing; inspection/sync use Python, Git, gh.

## Synchronize and clean

```sh
python3 tools/workspace.py sync                        # preview
python3 tools/workspace.py sync --project pipesor-iac  # one repo
python3 tools/workspace.py sync --apply                # explicit local changes
mani run status                                      # grouped Git inspection
```

Preview fetches and prunes origin's remote-tracking refs to obtain current evidence;
it leaves local branches and working files unchanged. Status is local-only and may
show stale tracking refs until fetched. An error/skip produces a nonzero exit code,
while other inventory repos are still inspected. Apply is not a cross-repo transaction.

Each checkout must have the exact inventory origin URL, a normal branch fetch
refspec, no pending Git operation and no uncommitted/untracked changes. Local main
must be an ancestor of the fetched origin/main; updates use fast-forward only.
Only feature/* and fix/* are cleanup candidates. Deletion requires a merged PR to
main in the same repository, exact local branch/head SHA equality, and the squash
commit reachable from the fetched main. Worktree branches are preserved. An active
branch without that evidence blocks checkout changes for that repo. Unknown
branches, docs/ and exceptional hotfix branches remain for manual review.

Deletion prints the branch SHA and PR number. Expected-SHA ref deletion rejects a
branch modified since inspection. Run while the selected repositories are idle;
Git cannot make this whole inspection-and-update sequence atomic against editors
and other Git clients. No reset, stash, push, PR merge or worktree removal occurs.
GitHub availability is required for cleanup. Fetch failure or insufficient evidence
keeps branches. A merged PR proves integration, not semantic equivalence after
conflict resolution or a later revert. Main CI is a separate quality signal.

`pipesor-docs` is deliberately outside the inventory: its external upstream and
existing work need a separate policy. To enroll a real microservice, add an explicit
project with path `../services/<repository>`, its Pipesor HTTPS URL and a `services`
tag. This requires a reviewed manifest change; directory discovery is not automatic.

## Contribution and verification

Follow [organization policy](https://github.com/pipesor-org/.github/blob/main/CONTRIBUTING.md):
short-lived feature/fix branches from main, reviewed squash PRs and English
Conventional Commits. PR title/body form the prospective squash message; include
migration notes and any BREAKING CHANGE footer in the description. Repository
squash settings must be configured when the remote is created.

```sh
python3 -m unittest discover -s tests -v
git diff --check
```

Tests create temporary Git remotes/checkouts and mock only GitHub responses.
They cover squash cleanup, preview, repeated runs, extra local commits, closed
unmerged PRs, dirty files, diverged main, worktrees and missing squash evidence.
CI runs these tests and Commitlint; it never synchronizes developers' checkouts.
The first commit of a new repository bootstraps main; subsequent work uses PRs.

## Codex

See [.codex/README.md](.codex/README.md). Shared guidance lives in AGENTS.md;
optional project configuration stays local. A sibling .codex directory does not
configure every Pipesor checkout automatically.
