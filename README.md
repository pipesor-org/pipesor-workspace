# Pipesor workspace

Development tooling for sibling Pipesor repositories. Shared CI lives in
[pipesor-ci](https://github.com/pipesor-org/pipesor-ci).
Each repository keeps its own Git history, reviews, hooks and releases. The sync
utility updates local checkouts; it does not deploy environments or establish a
compatible release across services.

## Current scope

The inventory covers `pipesor-frontend`, `pipesor-iac`, `pipesor-landing`,
`pipesor-docs`, `pipesor-ci`, `pipesor-validation-scenarios`, `.github` and the
explicitly enrolled repositories under
`services/`: `pipesor-evidence-correlation-engine`,
`pipesor-ingestion-normalization-service`, `pipesor-log-event-service`,
`pipesor-nlp-log-processor`, `pipesor-pipeline-service`,
`pipesor-rag-retrieval` and `pipesor-recommendation-service`. It does not
include this workspace repository. Services must be added explicitly before
they can be inspected or synchronized; directory discovery is not automatic.

## Requirements and setup

Place this checkout beside `pipesor-frontend`, `pipesor-iac`, `pipesor-landing`,
`pipesor-docs`, `pipesor-ci`, `pipesor-validation-scenarios`, `.github`, and `services/`. Install Python 3.10+, Git and GitHub CLI.
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
python3 tools/workspace.py sync --prune-worktrees      # preview, including merged worktrees
python3 tools/workspace.py sync --apply --prune-worktrees
python3 tools/workspace.py sync --apply --prune-worktrees --include-workspace  # everything
python3 tools/workspace.py status                      # inspect local status
```

Review the preview before applying it: apply can switch the primary checkout to
`main`, fast-forward `main` and delete the merged local branches listed in the
preview; with `--prune-worktrees` it also removes the linked worktrees that hold
them. Preview fetches and prunes origin's remote-tracking refs to obtain current
evidence; it leaves local branches, worktrees and working files unchanged. Status is
local-only and may show stale tracking refs until fetched. An error/skip produces a
nonzero exit code, while other inventory repos are still inspected. Apply is not a
cross-repo transaction.

Status groups repositories by uncommitted changes, other branches with clean
working trees, clean working trees on main, and unavailable repositories, in that
order. Each group shows its repository count, branch/tracking information and
changed files. Clean describes only the working tree: it does not prove that a
branch is synchronized with GitHub. `--project` limits the report to one repository.

Sync labels preview versus apply at the start. Preview uses “Would switch/update/
remove/delete”; apply reports actions only after they succeed. A repository is
listed by name only when it has something to report; repositories already up to
date with nothing kept are grouped on one “Up to date, nothing kept” line. Anything
sync leaves in place is printed as “Kept …” with its reason; for a branch without
merge evidence the reason says whether it has an open PR, a merged PR with a
different head, a PR closed without merge or no PR, how many commits it has that
are not in `origin/main`, and whether it is local only, the same as origin or
different from origin. Each run ends with counts of
repositories with planned/completed changes, unchanged repositories, and
skipped/failed repositories. A failed apply can be partial: completed actions stay
in place and are listed above the failure; there is no automatic rollback. Inspect
the repository and resolve the reported reason before retrying. Review preview
before using `--apply`, retaining the same `--project` and `--prune-worktrees`
selection when applicable.

A repository is skipped only when the inventory path is not its primary checkout,
its origin URL or fetch refspec differ from the inventory, or local `main` is ahead
of or diverged from the fetched `origin/main`. Otherwise each step is decided on
its own, so a dirty checkout or an unmerged active branch no longer blocks the rest:

- **Update main** (fast-forward only). If `main` is not checked out anywhere, its
  ref moves directly, guarded by the inspected SHA. If it is checked out in the
  primary checkout or a linked worktree, that checkout is fast-forwarded only when
  it is clean and has no pending Git operation; otherwise `main` is kept behind.
- **Delete merged branches.** Local branches of any name except `main` are
  candidates. Deletion requires a merged PR to main in the same repository, exact
  local branch/head SHA equality, and the squash commit reachable from the fetched
  main. Branches without that evidence are kept and listed.
- **Active branch.** A verified branch checked out in the primary checkout is
  switched to `main` and deleted when the checkout is clean, idle, and `main` is not
  checked out in another worktree. Otherwise the branch and its files stay as they are.
- **Linked worktrees.** A verified branch checked out in a linked worktree is kept
  and reported unless `--prune-worktrees` is given. With the flag, the worktree is
  removed (`git worktree remove`, never `--force`) and the branch deleted, only if
  the worktree is unlocked, clean (no untracked files), idle and still at the PR
  head SHA. Ignored files inside it, such as `node_modules/` or `.venv/`, are
  deleted with it. Detached worktrees, such as bootstrap tooling copies, are not touched.

If something is kept for uncommitted or untracked work, inspect it with
`git -C <path> status` and decide how to preserve that work before retrying. If a
branch has no verified merged PR, inspect its commits and PR status first. The PR
may still be open or unmerged, the local branch may have extra commits, or the
required merge evidence may be unavailable. Keep the branch until its work and
merge status are understood; then rerun the preview.

Deletion prints the branch SHA and PR number. Expected-SHA ref updates reject a
branch or `main` modified since inspection, and each worktree is re-checked right
before removal. On Linux and WSL, sync prints a warning (it does not block) when
another process, such as an editor, shell or coding agent, has its working
directory inside a checkout it is about to switch, fast-forward or remove; this
script and the shell that started it are excluded. Run while the selected repositories are idle; Git cannot make this
whole inspection-and-update sequence atomic against editors and other Git clients.
No reset, stash, push, PR merge or forced worktree removal occurs.
GitHub availability is required for cleanup. Fetch failure or insufficient evidence
keeps branches. A merged PR proves integration, not semantic equivalence after
conflict resolution or a later revert. Main CI is a separate quality signal.

This workspace repository is not in the inventory. Add `--include-workspace` to
sync it too, with the same rules, after the inventory repositories; run the command
from the primary checkout (not a linked worktree) so the inventory paths resolve.
Without the flag, update it separately: first inspect `git status` and ensure that
switching branches will not discard or overwrite your work, then run
`git switch main` and `git pull --ff-only`, which do not clean up local branches.

To enroll a real microservice, add an explicit
project with path `../services/<repository>`, its Pipesor HTTPS URL and a `services`
tag. This requires a reviewed manifest change; directory discovery is not automatic.

## Shared CI

The shared Java automation that lived here (`actions/java-verify` and
`publish-container.yml`) moved to [pipesor-ci](https://github.com/pipesor-org/pipesor-ci),
which now owns the reusable conventions, container build, image assurance,
publication and release components for every repository. Existing consumers
pinned to an earlier workspace commit keep resolving that commit; new changes go
to pipesor-ci.

## Local runtime stack

`local-stack/` and `tools/local_stack.py` implement the `local` profile of the
[runtime manifest](https://github.com/pipesor-org/pipesor-docs/blob/main/docs/development/runtime-manifest.md):
PostgreSQL 17 with pgvector (host port `55432`, database `pipesor`) and the Azure
Service Bus emulator (AMQP on `5672`) with topic `workflow-evidence` and the
`pipeline`, `log-event` and `nlp` filtered subscriptions. Requires Docker with
Compose and `openssl`.

```sh
python3 tools/local_stack.py init    # keys, credentials, env/<workload>.env (0600)
python3 tools/local_stack.py up      # start and wait for the containers
python3 tools/local_stack.py status
python3 tools/local_stack.py down    # stop; volumes are kept
```

Build each Java service once in its checkout (`./mvnw -B -ntp -DskipTests package`), then
run them against the stack with their generated env files:

```sh
python3 tools/local_stack.py services-start            # or name workloads, e.g. pipeline correlation
python3 tools/local_stack.py services-ps
python3 tools/local_stack.py services-stop
```

PIDs and logs live in `~/.local/state/pipesor-local/` (override with `PIPESOR_LOCAL_STATE`).
Ports: ingestion 8081, pipeline 8082, log-event 8083, nlp 8084, correlation 8085,
recommendation 8086, rag 8087, dashboard 5173. NLP and RAG are started from their own
repositories with `~/.config/pipesor-local/env/{nlp,rag}.env`.

`init` is idempotent and writes only under `~/.config/pipesor-local/` (override with
`PIPESOR_LOCAL_HOME`): one RSA signing key per workload plus the session key, random
stack passwords, and one env file per service with its audience, trusted callers,
outbound endpoints, database and messaging settings. Load a service's settings with
`set -a; . ~/.config/pipesor-local/env/<workload>.env; set +a` before starting it
from its repository. Nothing generated is committed. The emulator caps message TTL at
one hour and duplicate detection at five minutes, so its settings differ from Azure
there only. Its container log prints the local SQL password; treat the log as local.

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
Shared CI tests parse the JSON-compatible YAML and exercise publisher shell steps
with simulated Docker responses, including context, checksum, revision and digest
failures. Ubuntu/Bash publisher tests are skipped on native Windows. They do not
perform a hosted workflow or registry integration.
The first commit of a new repository bootstraps main; subsequent work uses PRs.

## Codex

See [.codex/README.md](.codex/README.md). Shared guidance lives in AGENTS.md;
optional project configuration stays local. A sibling .codex directory does not
configure every Pipesor checkout automatically.
