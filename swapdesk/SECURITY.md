# Security Policy

SwapDesk is non-custodial (it never holds funds) but bugs here can still
cost users money: a wrong deposit address shown, a memo dropped, a secret
leaked from local storage, or a provider response trusted when it shouldn't
be.

## Reporting a vulnerability

**Do not open a public GitHub issue for a vulnerability that isn't already
public.** Instead:

1. Go to this repository's **Security** tab and use **"Report a
   vulnerability"** (a private Security Advisory). This is the preferred
   channel.

If that's ever unavailable, open a minimal public issue with **no exploit
details** (just "I found a security issue, please contact me") and we'll
follow up to move the conversation private.

Please include:
- What's affected (which provider/coin path, which file/function if you know it)
- Impact (funds misdirected? secret exposed? DoS only?)
- Steps to reproduce, or a PoC if you have one
- Your suggested severity, if you have a view

We'll acknowledge within a reasonable window and coordinate a fix and
disclosure timeline with you before anything goes public.

## Scope

In scope:
- `app.py`, `ui/` (every tab mixin in the package), `providers/` (every
  module in the package), `config.py`, `preflight.py`,
  `widgets.py`, `theme.py` and the launcher scripts
- `ui/deposit.py` deserves particular attention: it renders the deposit
  address and memo, decides when the QR is suppressed, gates the copy
  buttons behind the memo and address-verification acknowledgments, runs
  the live dry-run quote check, and performs the post-creation destination
  verification. A defect there misdirects funds as surely as one in
  `providers/`
- Anything that could misdirect funds, leak `data/config.json` contents, or
  let a malicious/compromised provider response corrupt local state in an
  exploitable way

Out of scope:
- Vulnerabilities in the swap providers or protocols themselves (Trocador,
  SideShift, ChangeNOW, FixedFloat, 0x, Chainflip, THORChain, Maya
  Protocol), report those to the provider or protocol directly
- Social engineering, physical access to an unlocked machine
- Issues that require the user to have already pasted a malicious address
  themselves

## Known, already-accepted risk (not a new finding)

- `data/history.json` is **never encrypted**, with or without a master
  password. The master password covers the credential store only. History
  records provider, timestamp, deposit address and destination address per
  swap, plus the FixedFloat order access token, under OS file permissions
  alone. This is documented in the README and in `config.py`, and
  `auto_clear_history_days` defaults to 90 because of it. A report that
  history is unencrypted, without a way to read it cross-user or
  cross-process, is not a new finding.
- Provider API keys/secrets can be stored **encrypted** at rest: set a
  master password and they live in `data/config.enc` (AES-256-GCM under a
  scrypt-derived key; see `secretbox.py`), which is the recommended path and
  is offered on first run. If you opt out, `data/config.json` stores them in
  **plaintext**, protected only by OS file permissions (chmod 600 / icacls).
  This is documented in the README and in `config.py`. A report of "the
  config file isn't encrypted" when master-password encryption was not
  enabled, without a way to actually read it cross-user/cross-process, is
  not a new finding.
