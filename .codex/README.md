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
