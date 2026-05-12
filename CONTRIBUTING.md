# Contributing to guise

Thanks for your interest in guise.

## Reporting bugs and requesting features

Open an issue with a clear description and reproduction steps. For features,
explain the use case so the trade-off can be weighed against keeping guise
small.

For **security issues**, do not open a public issue — see `SECURITY.md`.

## Development setup

```bash
git clone <your fork>
cd guise/server
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests/ -q
```

The unit tests mock `subprocess.run` and `imaplib.IMAP4_SSL` — they do not
require a running mailserver, a container runtime, or network access.

For integration testing against a live docker-mailserver instance, build the
image (`make build`) and deploy it as a sidecar in your mailserver's compose
project. See the top-level `README.md` for the compose snippet.

## Code style

- **Python**: PEP 8, 4-space indent, type hints where they clarify intent
- **HTML templates**: 2-space indent (Jinja convention)
- **No bypassing safety primitives**: no `|safe` in templates, no `shell=True`
  in subprocess calls, no hand-rolled HTML escaping

## Commit conventions

- Short imperative subject (≤ 72 chars), e.g. *"Add CSRF protection"*, not
  *"Added CSRF..."* or *"This commit adds..."*
- Body explains the *why*, not the *what* — the diff already shows what
- Reference issues with `Closes #N` (one per line if multiple — issue
  trackers only parse the first directive per line)

## Pull requests

- Include or update tests for behaviour changes
- Run `pytest tests/ -q` from `server/` before submitting
- Keep PRs focused — one concern per PR

## License

By contributing, you agree that your contributions will be licensed under the
project's AGPL-3.0 license. See `LICENSE`.
