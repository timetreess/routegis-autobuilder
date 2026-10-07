# Contributing

## Development setup

```powershell
python -m venv .venv
& ".venv\Scripts\python.exe" -m pip install -e ".[test]"
& ".venv\Scripts\python.exe" -m pytest
```

Python 3.9 or later is required. Keep credentials in an ignored `.env`; never commit API keys, project drawings or generated project outputs.

## Pull requests

1. Open an issue before a substantial behavior or data-model change.
2. Keep changes focused and include tests for important behavioral boundaries.
3. Run the full pytest suite and `uv run scripts/build_plugin.py`.
4. Confirm the generated plugin ZIP contains no `.env`, bytecode or native binaries.
5. Describe any QGIS version used for manual verification.

Maintainers create version tags and publish GitHub or official QGIS releases. Follow the [release guide](docs/RELEASING.md) to run the non-publishing preflight and prepare the distribution artifacts.
