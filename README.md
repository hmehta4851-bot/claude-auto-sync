# Claude Auto-Sync

Keeps Claude Code, Gemini CLI, and Codex CLI in sync — even when your Mac is off.

## How it works

**GitHub Actions** (every 6 hours, 24/7):
- Polls npm registry for new versions of Claude Code, Gemini CLI, Codex CLI
- Updates `versions.json` with latest known versions
- Checks if the Claude Code npm package ships new bundled skills → commits them to `new-skills/`

**Mac LaunchAgent** (on every boot + every 6 hours while on):
- Fetches `versions.json` from this repo
- If a newer version exists → runs `npm update -g` to install it
- Syncs skills and MCPs across all three tools

## Files

| File | Purpose |
|---|---|
| `versions.json` | Latest CLI versions detected by GitHub Actions |
| `mcps.json` | Full MCP server configuration (25 servers) |
| `new-skills/` | Any new skills extracted from npm packages |
| `.github/workflows/sync.yml` | The scheduled Actions workflow |

## Manual trigger

Go to **Actions → Check for CLI Updates → Run workflow** to force an immediate check.
