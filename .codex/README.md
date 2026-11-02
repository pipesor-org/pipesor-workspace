# Codex scope

`AGENTS.md` contains the working agreements for this repository. Optional project
configuration can be created by copying `config.example.toml` to the ignored
`config.toml`. The example adds only the public OpenAI documentation MCP server.

Project `.codex/config.toml` loads only for trusted projects. This directory does
not automatically configure sibling repositories. Start Codex in the intended
repository and follow its own AGENTS.md. Use personal `~/.codex/config.toml` for
user defaults; do not redirect CODEX_HOME to this repository or commit credentials,
authentication state, machine-specific paths, permissions, or account settings.

References:
- https://developers.openai.com/codex/config-basic/
- https://developers.openai.com/codex/guides/agents-md/

## Shared personal defaults

For Codex CLI sessions that need to work across the Pipesor checkouts, each
collaborator may add the following to their existing personal `~/.codex/config.toml`:

```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
```

The values are also in `user-config.example.toml`. Merge them into existing TOML;
do not replace the file, duplicate a table, copy credentials, or change CODEX_HOME.
These personal defaults apply to other projects too. Project settings, CLI flags,
and managed requirements may override or restrict them. Network access permits
outbound connections generally, not only GitHub. Workspace-write permits writes
within the session workspace; start Codex from the Pipesor parent directory if
work spans sibling repositories. Permission for GitHub actions still depends on
credentials and the task's authorization.

Restart Codex or resume a session without the old flags, then run
`python3 tools/workspace.py doctor` from this repository. It checks the GitHub API
using the active `gh` login; test from inside the session to verify its sandbox.
A successful API check does not establish write access to every repository.
