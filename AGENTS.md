# Workspace tooling

Read README.md and follow the organization contribution policy in
https://github.com/pipesor-org/.github/blob/main/CONTRIBUTING.md.

Use short-lived feature/<topic> or fix/<topic> branches from main and reviewed
squash PRs into main. Use English Conventional Commits, including chore and !.
Run `python3 -m unittest discover -s tests -v` and `git diff --check` after changes.
Use Python's standard library, Git and gh; Mani owns the inventory. Keep the
JSON-compatible YAML format so no YAML runtime dependency is needed.

Shared CI lives in https://github.com/pipesor-org/pipesor-ci; do not add reusable
workflows or actions here.

Sync previews by default. Never reset, stash, push, merge PRs or deploy through the
synchronization utility. Delete local branches only with exact merged-PR head
evidence and a reachable squash commit, regardless of name. Remove a linked
worktree only behind the opt-in --prune-worktrees flag, for such a verified branch,
when the worktree is clean, unlocked and idle; never force removal. A blocked step
must be reported as kept and must not stop the safe steps of the same repository.
Read each sibling repository's own instructions before touching it. Do not write
user/global Codex or Git configuration. Do not silently enroll services or docs.
