# Contributing

Thanks for looking at notifier. Keep changes small, and cover them with tests.

## Setup

Python 3.10 or newer. The runtime dependencies are the standard library and PyYAML.

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
```

Tests are offline. HTTP is mocked with a local `http.server` or a monkeypatch, and they do not call the network.

## Guidelines

- Do not add secrets, tokens, or personal data to the repo, tests, fixtures, or logs.
- Webhook URLs and bot tokens belong in environment variables referenced as `${NAME}`. Anything new that prints a line has to pass through the redactor.
- A failed post must not record the id. The same item has to still be pending on the next run while it is inside the grace window.
- Discord deliveries send `allowed_mentions.parse: []` unless that item opts in. Do not drop that field.
- `notifier list` may only print ids and timestamps that are in the schedule or the state file.
