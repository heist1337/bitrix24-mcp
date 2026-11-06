# Development

Keep this repository independently runnable with uv and native FastMCP.
Preserve domain guards and uncertain-outcome handling. No credentials or provider
payloads in Git, logs or command arguments. Live acceptance uses bounded reads;
never send messages or change business data for testing.
Run `uv sync --locked`, `uv run ruff check src tests`,
`uv run python -m unittest discover -b -s tests` and `uv build`.
Use task branches and PRs after initial bootstrap.
