# elearning MCP

Independent, local stdio MCP server using the official Python SDK (`mcp==1.30.0`, MIT). No Stella import, Plugin Runtime or proprietary remote backend is required. Business code and state belong to this repository; extracted from Stella-Plugins f5eed89 without changing existing account, cursor, cookie or manifest formats.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python mcp_server.py --data-dir /absolute/private/elearning --user-id local-default
```

Use Python 3.11 or newer. The optional `--legacy-dir` points to the former briefing settings directory for one-time, non-destructive adoption. Existing migrated data is used in place; original downloaded files and previous source files are not removed. Use a separate data directory and process per identity. Startup paths are trusted local configuration; tools cannot override them.

The server publishes ordinary MCP tools, with read-only annotations, strict argument names, structured results and protocol errors. Call `get_settings` to inspect public settings. Credentials are masked. Configure accounts through `save_settings`; settings operations are intended for the local owner. Stella excludes these management operations from Agent discovery and routes its authenticated settings/briefing through the same managed MCP connection. Stella keeps its execution permissions; independent clients must enforce their own policy.

`sync_files` requires a configured existing directory and selected courses. It retains the two previous layouts, manifests, duplicate detection, versioned file publication, directory and symlink restrictions, cancellation and failure cleanup. Login may use Playwright, so install the existing crawler dependencies and a browser when needed. The pre-existing `python main.py` CLI remains supported with its own config/.env profile; the MCP profile is explicitly bound to --data-dir and never reads the CLI .env implicitly.
