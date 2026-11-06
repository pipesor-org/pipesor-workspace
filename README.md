# Pipesor workspace

Development tooling for sibling Pipesor repositories. Each repository keeps its own
Git history, reviews, hooks and releases. This workspace updates local checkouts;
it does not deploy environments or establish a compatible release across services.

## Current scope

The inventory covers `pipesor-frontend`, `pipesor-iac`, `pipesor-landing`,
`pipesor-docs`, `.github` and the explicitly enrolled repositories under
`services/`: `pipesor-evidence-correlation-engine`,
`pipesor-ingestion-normalization-service`, `pipesor-log-event-service`,
`pipesor-nlp-log-processor`, `pipesor-pipeline-service`,
`pipesor-rag-retrieval` and `pipesor-recommendation-service`. It does not
include this workspace repository. Services must be added explicitly before
they can be inspected or synchronized; directory discovery is not automatic.

## Requirements and setup

Place this checkout beside `pipesor-frontend`, `pipesor-iac`, `pipesor-landing`,
`pipesor-docs`, `.github`, and `services/`. Install Python 3.10+, Git and GitHub CLI.
Authenticate with `gh auth login` using an account with access to the private repos.
Use [Mani 0.32.1](https://github.com/alajmo/mani/releases/tag/v0.32.1)
when you need to clone missing inventory repositories or run Mani tasks. If
installed in the parent workspace's `.tools/bin`, add that directory to `PATH`
or invoke `../.tools/bin/mani` from this repository (Windows: `..\.tools\bin\mani.exe`).

Run commands from this repository's root, using the instructions for your shell.

### WSL and Linux (Bash)

In WSL, install the Linux versions of Python, Git, gh, Mani and the contributor
tools inside your WSL distribution. Keep this workspace and its sibling checkouts
together in the WSL filesystem, for example under `~/code/pipesor/`. Authenticate
with `gh auth login` inside WSL. Use `python3`; a `python` alias is not required.

```sh
python3 tools/workspace.py status
python3 tools/workspace.py doctor
python3 tools/workspace.py sync  # preview
```

### Native Windows (PowerShell)

Install the Windows versions of the tools and put them on `PATH`. Install Python
3.10+ with the `py` launcher, and authenticate with `gh auth login` in PowerShell.
Keep the workspace and sibling checkouts together, for example under
`C:\code\pipesor\`. Check `py -3 --version` before running the utility.

```powershell
py -3 tools/workspace.py status
py -3 tools/workspace.py doctor
py -3 tools/workspace.py sync  # preview
```

For all later examples, use `py -3` in place of `python3` in PowerShell.
Use the commands and tool installations belonging to the environment you are
working in; Windows installations do not replace the Linux tools needed in WSL.

### Inventory and contributor tools

`mani sync` clones missing repositories;
review the manifest before cloning. `mani run status --all` runs the inventory's grouped
Git status task. Both commands require Mani on `PATH`.
`mani sync` does not update existing main branches. `mani.yaml` uses JSON syntax, which is
valid YAML, so the Python utility reads the same inventory without extra packages.
[Mani configuration](https://manicli.com/config/) defines paths relative to this file.
The checked-in VS Code workspace opens the sibling repositories and `services/`.

For contributing to this repository, install Bun 1.4.4 and standalone Lefthook
2.2.2 for your environment, verifying the binary against the official release
checksums, then run in either shell:

```sh
bun install --frozen-lockfile
bun run setup:hooks
```

Run tests in WSL/Linux with `bun run test`, or in Windows PowerShell with
`bun run test:windows`. These use `python3` and `py -3`, respectively.

Run `python3 tools/workspace.py doctor` to check tool availability, GitHub
authentication and API connectivity. The current doctor checks Git, gh, Mani,
Python, Bun, Lefthook and Codex and exits with an error if any is missing. Mani,
Bun, Lefthook and Codex are not required by the Python `status` and `sync`
commands themselves. Doctor does not install software, change global configuration
or prove access to every repository.

Doctor separates core tools, additional tools (with their purpose), and GitHub
authentication/API checks, then reports an overall result. A missing additional
tool still makes doctor exit nonzero, but does not prevent running Python
status/sync when their own requirements are available. Install missing tools in
the environment you are using (WSL/Linux or native Windows).

## Synchronize and clean

Preview sync before starting work from `main` or after a PR merges when you want
your local checkout to include the latest changes. Use the preview, rather than
local-only status, to see whether `main` is behind the current remote.

```sh
python3 tools/workspace.py sync                        # preview
python3 tools/workspace.py sync --project pipesor-iac  # preview one repo
python3 tools/workspace.py sync --apply                # apply local changes
python3 tools/workspace.py status                      # inspect local status
```

Review the preview before applying it: apply can switch a checkout to `main`,
fast-forward it and delete the merged local branches listed in the preview.
Preview fetches and prunes origin's remote-tracking refs to obtain current evidence;
it leaves local branches and working files unchanged. Status is local-only and may
show stale tracking refs until fetched. An error/skip produces a nonzero exit code,
while other inventory repos are still inspected. Apply is not a cross-repo transaction.

Status groups repositories by uncommitted changes, other branches with clean
working trees, clean working trees on main, and unavailable repositories, in that
order. Each group shows its repository count, branch/tracking information and
changed files. Clean describes only the working tree: it does not prove that a
branch is synchronized with GitHub. `--project` limits the report to one repository.

Sync labels preview versus apply at the start. Preview uses “Would switch/update/
delete”; apply reports actions only after they succeed. Each run ends with counts
of repositories with planned/completed changes, unchanged repositories, and
skipped/failed repositories. A failed apply can be partial: completed actions stay
in place and are listed above the failure; there is no automatic rollback. Inspect
the repository and resolve the reported reason before retrying. Review preview
before using `--apply`, retaining the same `--project` selection when applicable.

Each checkout must have the exact inventory origin URL, a normal branch fetch
refspec, no pending Git operation and no uncommitted/untracked changes. Local main
must be an ancestor of the fetched origin/main; updates use fast-forward only.
Local branches of any name are cleanup candidates, except main. Deletion requires
a merged PR to main in the same repository, exact local branch/head SHA equality,
and the squash commit reachable from the fetched main. Worktree branches and
branches without that evidence are preserved. An active branch without that
evidence blocks checkout changes for that repo.

If a repository is skipped for uncommitted or untracked work, inspect it with
`git -C ../<repository> status` and decide how to preserve that work before
retrying. If the active branch has no verified merged PR, inspect its commits and
PR status first. The PR may still be open or unmerged, the local branch may have
extra commits, or the required merge evidence may be unavailable. Keep the branch
until its work and merge status are understood; then rerun the preview. A skip in
one repository does not prevent the others from being inspected or applied.

Deletion prints the branch SHA and PR number. Expected-SHA ref deletion rejects a
branch modified since inspection. Run while the selected repositories are idle;
Git cannot make this whole inspection-and-update sequence atomic against editors
and other Git clients. No reset, stash, push, PR merge or worktree removal occurs.
GitHub availability is required for cleanup. Fetch failure or insufficient evidence
keeps branches. A merged PR proves integration, not semantic equivalence after
conflict resolution or a later revert. Main CI is a separate quality signal.

This workspace repository is not in the inventory and does not synchronize itself.
To update it separately, first inspect `git status` and ensure that switching
branches will not discard or overwrite your work, then run:

```sh
git switch main
git pull --ff-only
```

These commands do not clean up the workspace's local branches. To enroll a real microservice, add an explicit
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

In Windows PowerShell, run:

```powershell
py -3 -m unittest discover -s tests -v
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
