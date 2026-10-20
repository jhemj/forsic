# web/ + hermes_cli/web_routers/ — the dashboard (`hermes dashboard` → `/chat`)

Applies on top of the root `AGENTS.md`. Backend routers: `hermes_cli/web_routers/*.py`, one file per
dashboard surface, mounted by `hermes_cli/web_server.py` (+ `web_server_*.py` siblings). Frontend:
`web/src/`. Shared JSON-RPC/WS client: `apps/shared` (`@hermes/shared`), also used by the desktop.

## Forsic chat presentation

The user-authorized Forsic dashboard presents the transcript and composer in React. `ChatPage.tsx`
attaches to the existing native conversation through the shared gateway; Hermes still owns the
agent loop, goals, interruption, approvals, compaction, and persistence. Do not add a second
executor or create/resume an agent while merely loading a conversation.

The dashboard uses the React conversation and case monitor; it does not embed a separate TUI
analysis view. Existing authentication and native session identities remain authoritative.
Persist user pauses through native goal control before interrupting; an ordinary refresh must
never resume them.

## `dashboard` vs `serve`

`dashboard` and `serve` share `cmd_dashboard` / `start_server` but are independent surfaces — neither
launches the other. `serve` is the headless backend the desktop app spawns (`headless_backend=True`:
`cmd_dashboard` skips `_build_web_ui` and exports `HERMES_SERVE_HEADLESS=1` so `mount_spa()`
disables the SPA even if a stray `web_dist/` exists — only JSON-RPC/WS/API is reachable). The
desktop has no build/runtime dependency on this frontend. Details: `apps/desktop/src/AGENTS.md`.

## Rules

- Auth: every new REST route and WS endpoint uses the same session token; never a second scheme.
- Routers are one-file-per-surface; a new surface is a new `web_routers/<surface>.py`, not a growing
  `web_server.py`.
- Tests: Python in `tests/hermes_cli/` (routers, pty bridge); JS in the `web/` vitest suite. Python
  tests must not assert about `package.json` / `.tsx` sources (root testing rules). Root TypeScript
  style rules apply.
