# Contributing

Install the package with its test extra and run the suite:

```bash
pip install -e ".[dev]"
python -m pytest
```

The package targets Python 3.11 and later and has no third-party runtime dependencies. Keep new behavior covered by a test in `tests/`.

Open a pull request against `main`. Describe what changed and how you tested it.
