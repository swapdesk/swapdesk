# Contributing to SwapDesk

SwapDesk is non-custodial, but a bug here can still send someone's funds to
the wrong place. Changes are reviewed with that in mind.

## Reporting bugs

Open a GitHub issue with your OS, Python version, the SwapDesk version
(`SwapDesk/VERSION`) and the provider involved. Remove API keys, addresses and
order tokens from anything you paste.

**Security issues are different.** Do not open a public issue; follow
[SECURITY.md](SECURITY.md).

## Setting up

Python 3.12 or newer.

```bash
cd SwapDesk
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.txt
```

## Before sending a change

From `SwapDesk/`:

```bash
python -m compileall -q .
ruff check .
python -m unittest discover -s tests -v
```

CI runs the same three steps (`.github/workflows/ci.yml`).

## Ground rules

- Keep changes small and focused. Do not mix cleanup with behaviour changes
  to providers, address validation, memo handling or pre-flight checks.
- Do not weaken a safety check to make a test pass. If a check is wrong, fix
  it and add a regression test that fails without the fix.
- Dependencies stay hash-pinned. Regenerate with
  `pip-compile --generate-hashes`; do not hand-edit `requirements.txt` or
  `requirements-build.txt`.
- Never commit API keys, tokens, wallet secrets, `data/` contents or
  personal paths. `tests/test_release_hygiene.py` checks for the common
  shapes, but it is a backstop, not a review.
- SwapDesk has no bundled keys, no hosted API server and no AI/LLM features.
  Changes that add them will not be accepted.
- Add or update a `CHANGELOG.md` entry for user-visible changes. The version
  lives only in `SwapDesk/VERSION`.

## License

By contributing you agree your work is released under the repository's MIT
license.
