# Workspace tooling and shared CI

Read README.md and follow the organization contribution policy in
https://github.com/pipesor-org/.github/blob/main/CONTRIBUTING.md.

Use short-lived feature/<topic> or fix/<topic> branches from main and reviewed
squash PRs into main. Use English Conventional Commits, including chore and !.
Run `python3 -m unittest discover -s tests -v` and `git diff --check` after changes.
Use Python's standard library, Git and gh; Mani owns the inventory. Keep the
JSON-compatible YAML format so no YAML runtime dependency is needed.

Shared Java CI components live in actions/java-verify and
.github/workflows/publish-container.yml; their contracts and adoption limits are
owned by README.md. Keep Java setup and service-specific checks in callers. The
publisher consumes the same run's tested image without caller scripts, checkout,
rebuild, arbitrary inputs or inherited secrets; preserve caller-derived image
identity, push/main/act guards and candidate schema 1. Consumers pin a reviewed,
published full commit SHA. Do not enable private access, publish revisions or
change repository settings implicitly. Mocked shell checks do not prove hosted
GitHub/GHCR integration.

Sync previews by default. Never reset, stash, push, merge PRs, deploy, or delete
worktrees through the synchronization utility. Delete local branches only with
exact merged-PR head evidence and a reachable squash commit, regardless of name.
Read each sibling repository's own instructions before touching it. Do not write
user/global Codex or Git configuration. Do not silently enroll services or docs.
