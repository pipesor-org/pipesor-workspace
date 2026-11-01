# Workspace tooling

Read README.md and follow the organization contribution policy in
https://github.com/pipesor-org/.github/blob/main/CONTRIBUTING.md.

Use short-lived feature/<topic> or fix/<topic> branches from main and reviewed
squash PRs into main. Use English Conventional Commits, including chore and !.
Run `python3 -m unittest discover -s tests -v` and `git diff --check` after changes.
Use Python's standard library, Git and gh; Mani owns the inventory. Keep the
JSON-compatible YAML format so no YAML runtime dependency is needed.

Sync previews by default. Never reset, stash, push, merge PRs, deploy, delete
worktrees, or clean unknown branches through the synchronization utility.
Deletion requires exact merged-PR head evidence and reachable squash commit.
Read each sibling repository's own instructions before touching it. Do not write
user/global Codex or Git configuration. Do not silently enroll services or docs.
