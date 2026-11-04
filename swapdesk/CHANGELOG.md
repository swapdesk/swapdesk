# Changelog

Versions are alpha. See the README for current limitations.

**v0.1.32**: bundled keys and the SwapDesk API server removed; repository
cleaned up for release.

- Repository layout flattened to a single root: the nested
  `swapdesk-0.1.31-alpha-fixes/` folder is gone, and there is one README, one
  CHANGELOG (this file, moved from `SwapDesk/docs/`), one `SECURITY.md` and
  one `LICENSE`, all at the repo root. `requirements-build.txt` moved to the
  root and the three build scripts now install it from there. CI moved to
  `.github/workflows/ci.yml` and runs from `SwapDesk/`.
- Version is `0.1.32` (the `-alpha` suffix is dropped from the version string;
  the software is still alpha, see the README). `SwapDesk/VERSION` is the one
  source: the window title, `build.py` (Windows version resource) and the new
  `preflight.py --version` all read it.
- `CONTRIBUTING.md` and `.gitignore` added. CI now compiles every module, runs
  ruff (pinned) and the regression tests.
- New `tests/test_release_hygiene.py`: version consistency, repository layout
  and link checks, hash-pinned dependencies, no AI/LLM packages, imports or
  endpoints, no secret-shaped strings, personal paths, local config files or
  unreviewed binary assets in the tree.
- No change to provider behaviour, supported coins, validation or any
  transaction-safety check (see the audit follow-ups below for the one
  `config.py` change).
- Release audit follow-ups (second QC pass):
  - **History is no longer destroyed when it is damaged.** An unparseable
    `history.json` used to load as empty and be overwritten by the next swap,
    losing every earlier order ID. It is now renamed to
    `history.corrupt-<UTC time>.json` and a new history is started
    (`config.py`, `load_history`). I/O errors do not trigger the rename. The
    200-record cap is now documented in the README.
  - **Dependencies updated for published advisories**, hashes taken from PyPI
    for every artifact: `certifi` 2026.7.22 -> 2026.10.19 (emSign roots with
    compromised intermediates, PYSEC-2026-4320), `urllib3` 2.7.0 -> 2.8.0
    (streaming DoS and proxy-TLS advisories), `pillow` 12.3.0 -> 13.0.0
    (decoder/parser advisories; SwapDesk only opens QR images it generates
    itself). `pip-audit` reports no known vulnerabilities for
    `requirements.txt`. The declared Python floor of the pins is now 3.11.
  - GitHub Actions in `ci.yml` are pinned to commit SHAs (checkout v4.2.2,
    setup-python v5.6.0) and CI now runs on Python 3.12, 3.13 and 3.14.
  - A stale `release.yml` mention in `requirements-build.txt` now names the
    build scripts that actually install it; `.gitignore` gains tool caches.
  - New tests: `tests/test_order_safety.py` (mocked HTTP: a 500/502/504 or
    read timeout on an order-creating request is never retried, retries are
    bounded, redirects are not followed, malformed create-swap responses are
    rejected) and `tests/test_history_recovery.py` (damaged-history handling,
    the 200-record cap, minimum versions of the three audited dependencies,
    the QR image path under the pinned Pillow).
- Final release QC:
  - A `config.json` that does not parse no longer traps the user in the
    first-run "Protect your API keys" dialog. "Not now" and closing the
    window used to raise inside the dialog, so it could not be dismissed and
    the app never reached its "config unreadable" stop. Both now close it
    and the app exits with that message, leaving the file untouched
    (`app.py`).
  - SideShift: a timeout or gateway error while creating a shift is
    reported as a network failure again, so the deposit window says the
    order may exist at SideShift and must not be funded. It was re-raised as
    a plain provider error and lost that warning (`providers/sideshift.py`).
  - A destination echo that is not a string (an object or a list where an
    address belongs) is a SAFETY ABORT that names the order id, instead of a
    bare `AttributeError` with no order id (`preflight.py`).
  - The POSIX launchers and build scripts are executable again
    (`scripts/dev/*.sh`, `scripts/dev/SwapDesk.command`,
    `scripts/linux/build_linux.sh`, `scripts/macos/build_macos.command`).
    The documented double-click and `./scripts/dev/swapdesk.sh` paths failed
    without it.
  - Build-only: `setuptools` 84.0.0 -> 84.1.0 in `requirements-build.txt`
    (GHSA-xmj3-9gf7-h52w, Windows legacy wheel install path). Not shipped in
    the binary and not used to run from source.
  - `.gitignore` covers `permission_warning.txt`, which the app writes
    beside itself when it cannot lock down the data folder.
  - New tests: create-timeout error type for every order-creating provider,
    non-string destination echo, launcher shebangs and exec bits, and the
    `.gitignore` entries for files the app writes.

Removed features (unchanged from the earlier unreleased notes):

- Bundled keys removed: `bundled_keys.py`, `build.py --with-keys` (and
  `keys.local.json` / `SWAPDESK_*_API_KEY` baking), the Settings "Bundled
  keys" section and the Swap tab's bundled-key notice. SwapDesk uses only
  the provider keys you enter.
- SwapDesk API server client removed: `remote.py`, its Settings section,
  its row in the rate comparison and the preflight checks that existed only
  for it (including the `swapdesk` choice of `preflight.py --provider`). A
  `swapdesk_api` section in an existing `config.json` or `config.enc` is
  ignored and left on disk as it is.
- Saved configs, plaintext or encrypted, load unchanged.

**v0.1.31-alpha (security fixes)**: fixes applied to 0.1.31-alpha without a
version change.

- THORChain/Maya: the memo a node returns is now checked in full. The action
  must be a market swap (`=`, `SWAP`, `s`). The asset field must be the
  requested asset, as a full id or (THORChain only) its documented short
  name. The limit field must carry at least the requested 3% price
  protection. Whitespace or control characters abort. Previously only the
  destination field was read, so an add-liquidity or loan memo, a payout of a
  different chain's coin to the same 0x address, or a memo with no limit
  passed as verified.
- THORChain/Maya: the coin you send must be BTC, LTC, DOGE, BCH or DASH. ETH
  deposits must call the router contract (`depositWithExpiry`), and XMR and
  SOL deposits need memo handling this app cannot verify. These sources were
  shown as a plain vault transfer with a memo. They are now refused at quote
  time, at preflight and in `create_swap`, which also refuses any quote that
  returns a `router`.
- The deposit window withdraws its QR code and copy buttons once the order's
  deadline has passed, whether it opens expired or expires while open.
- `config._secure_write` scrubbed the outgoing `config.json`/`config.enc`
  before the atomic replace, so a replace that then failed destroyed the only
  copy of the keys. The old revision is now scrubbed through a hard link
  after the replace succeeds, and left alone where hard links are
  unavailable.
- Proxy mode "on" with a blank or malformed proxy URL ran every request
  direct with no error. It now fails closed, and Settings refuses to save it.
- BTC and LTC segwit addresses are fully decoded per BIP-173/BIP-350
  (checksum, witness version, program length). Previously any alphanumeric
  tail after `bc1`/`ltc1` was accepted.
- A 3xx response is an error rather than a result, since redirects are never
  followed.
- FixedFloat: an order whose refund leg (`back.tx`) is reported, or where a
  refund was chosen, with no payout transaction, is reported as refunded or
  refund in progress rather than Complete.
- Chainflip: `failed` together with a payout leg is Unknown (non-terminal)
  rather than Failed.
- A network failure while creating an order now says the order may exist at
  the provider and that nothing from that attempt should be funded.
- Malformed data fails cleanly instead of raising: NaN, Infinity, bool or
  out-of-range durations and expiry timestamps; non-object FixedFloat
  `from`/`to`/`time`; non-string or non-scalar order ids, memos and
  destination echoes; a non-object SwapDesk API create response; and a
  `history.json` that is not a list of objects (which blocked every swap at
  preflight).

**v0.1.31-alpha**: memo and status verification audit: the THORChain/Maya
destination check read against the protocol's documented memo layout, a
Trocador network guess that survived the coin-catalogue safety rule, a
Chainflip terminal status taken from an egress leg that had not landed, and
two UI defects found while tracing those paths.

- `providers/thorfork.py`'s post-quote memo check scanned every
  `:`-delimited field of the returned memo for the approved destination
  address. THORChain documents the memo as
  `SWAP:ASSET:DESTADDR:LIM/INTERVAL/QUANTITY:AFFILIATE:FEE`, with an
  optional refund target carried INSIDE the destination field as
  `DESTADDR/REFUNDADDR` (dev.thorchain.org/concepts/memos, and the worked
  quote example in THORChain's own swap guide). Two consequences, both
  fixed by reading the destination field by position and splitting the
  refund half off it:
  - A memo carrying `dest/refund` never matched any whole field, so every
    THORChain and Maya swap whose refund address survived the memo-budget
    ladder aborted with a SAFETY ABORT saying the destination was missing
    when it was not. Fail-closed, so no funds were at risk, but the only
    way past it was to clear the refund field.
  - A memo whose payout address was someone else's but which mentioned the
    approved address in another field (the affiliate field, or the refund
    half) passed as "independently confirmed". The payout position is the
    one that decides where the coins go, so it is the one checked now, and
    a refund address the user did not supply is refused rather than
    ignored: whatever sits there is where a failed swap returns the
    deposit.
- `Swap.refund_address_sent` for THORChain/Maya was derived from a
  `refund_address` key in the quote response. THORChain's documented quote
  schema has no such field, so it read None on every swap and the flag was
  False whenever a refund address had been entered -- and False is exactly
  what the deposit window renders its red "Refund address was NOT included,
  the memo would not fit" card on. It is now read from the memo that
  actually gets broadcast: True when the refund target is in it, False when
  the ladder shed it (the case the card exists for), and None when no
  refund address was asked for at all.
- `providers/trocador.py`'s `_net()` defaulted any ticker missing from its
  network table to `"Mainnet"`. `fetch_coins()` drops a curated ticker
  precisely when its known-native network is absent from the live `/coins`
  rows -- the safety rule added in 0.1.29 -- and this default then re-made
  that exact guess for the same ticker, on both the quote and the
  `/new_trade` path, for an app with no per-coin network selector to
  correct it with. An unroutable ticker is now reported as an unsupported
  pair and `create_swap()` refuses before placing an order. `preflight.py`
  reports Trocador pair support as a plain yes/no like every other
  provider now that there is no guess left to be inconclusive about.
- `providers/chainflip.py` read a terminal outcome out of a `completed`
  state without checking whether the egress leg had actually landed. Its
  own `_leg()` defines a leg with neither a `witnessedAt` time nor a
  transaction reference as still moving, and the `failed` branch already
  honoured that, so a payout in that state was reported as Complete ("Done.
  The coins were sent to your address"), written to history as final, and
  polling stopped with nothing on-chain to point at. An unsettled leg under
  `completed` now reports Sending (payout) or Refund in progress (refund),
  both non-terminal, and the next poll resolves it the moment either field
  appears.
- `ui/swap_tab.py`'s stale-fetch guard compared the coin pair but not the
  amount, although `_reset_quotes()` is bound to the amount field's
  `<KeyRelease>` as well as to both coin pickers. A quote fetch landing
  after the amount was edited put the rows back for an amount no longer on
  screen, auto-selected a provider on that basis, and left
  `on_create_swap()` checking the new amount against the old amount's
  min/max. The guard now covers both halves of the race it documents.
- The deposit window showed the deposit deadline as an instruction ("do not
  fund after this time") and then never checked it against the clock, so a
  window left open past it kept reporting "Waiting for your deposit to appear
  on-chain", which reads as still-fundable. Chainflip is the case that
  matters: its deadline is computed locally from the documented 24-hour
  channel lifetime and BaaS keeps reporting `waiting` afterwards, so nothing
  from the provider ever contradicts it. A non-terminal status whose deadline
  has passed now says the window closed, names the order id, and says to
  start a new swap. A deadline this app cannot parse is left alone rather
  than guessed at.
- `app.py` built the header divider and the proxy/DNS banner widgets inside
  `_refresh_route_pill()` rather than `_build_header()` (one level of
  indentation). That function runs again after every Settings save, so each
  save packed another divider and replaced the two banner widgets with
  fresh, unpacked ones while the `_*_banner_shown` flags kept describing the
  discarded pair -- a clearnet privacy warning can only work if the widget
  the flag refers to is the one on screen.

**v0.1.30-alpha**: cross-provider audit of the order-creation path: a
duplicate-swap retry gap, a stale-quote UI race, a dead credential-redaction
path, and a Windows key-permission gap in the keyed build script.

- Trocador, FixedFloat, SideShift, ChangeNOW and StealthEX's create-swap
  calls (`/new_trade`, `/create`, `/shifts/variable`, `/exchange`,
  `/exchange`) inherited the shared gateway-retry policy's full status set
  (`base.py`'s `_GATEWAY_STATUS`, including 500/502/504), which the
  networking layer's own comments already flag as unsafe for a call that
  creates a real order: after one of those, the order may already exist
  server-side, and the automatic retry could open a second one with a
  second, different deposit address. Chainflip's `/swap` was already
  narrowed to a request-not-processed-only retry set
  (`SWAP_RETRY_STATUSES = {408, 429, 503}`) after the 0.1.29 review; the
  same fix (a new shared `SwapProvider.ORDER_CREATE_RETRY_STATUSES`
  constant, passed as `retry_on=` at each create-order call site) is now
  applied to the other five providers that place a real order.
- `ui/swap_tab.py`'s `_show_quotes()` had no guard against a background
  quote fetch landing after the user had already changed the selected pair
  or amount while it was in flight: `_reset_quotes()` (bound to both coin
  pickers) clears `self.quotes`/`selected_provider` for the pair now on
  screen, but the stale fetch's callback then silently overwrote that with
  results -- and an auto-selected provider -- for the pair that was on
  screen when "Get best rate" was clicked. `app.py`'s `_apply_coin_refresh`
  already guards the analogous race for a coin-catalogue refresh; the same
  "did the pair on screen change under this response" check is now applied
  to a fresh quote fetch landing here.
- `providers/base.py`'s `_describe_network_error()` tried to redact a live
  query-string API key (StealthEX `api_key`, Chainflip `apiKey`) out of a
  network-error message before it could reach the status line, diagnostics
  pane, or crash.log, via `str(e).replace(str(url), self._safe_url(url))`.
  `url` never carries the query string at that point (request params are
  merged in separately, only at send time), so the `.replace()` could never
  match anything and the raw, unredacted exception text -- credential
  included, when the underlying request carried one and its exception type
  reached this fallback -- was returned as-is. This fallback (the other
  three branches in the same function already hand-write safe text rather
  than echo the exception) no longer interpolates the raw exception text at
  all.
- `build.py --with-keys`'s `write_baked()` wrote `keys_baked.py` with
  `os.open(..., 0o600)`, documented as matching `config.py`'s handling of
  the same class of secret. `0o600` is a POSIX-only mode; on Windows, NTFS
  access is governed by ACLs, not those bits, so the file was left at
  whatever permissions it inherited -- typically readable by every local
  account on the build machine -- for the duration of the build. Windows
  now gets the same `icacls`-based lockdown `config.py`'s `_lock_down()`
  already applies to `data/config.json`.

**v0.1.29-alpha**: destination-verification review: a QR gap in the deposit
window, a coin-catalogue safety rule that could pick the wrong chain, and a
cross-provider audit against each provider's live API documentation

- Chainflip deposit windows showed no deadline. Chainflip documents deposit
  channels as open for 24 hours, after which the network may no longer
  recognise a late deposit, so a user who sent the next day had no warning.
  The window now shows the deadline, counted from when the channel opened.
- Chainflip's `/swap` opens a deposit channel and is a GET, so the shared
  retry rule re-sent it after a 500, 502 or 504, when the first channel may
  already have been open. It is now retried only on 408, 429 and 503, which
  BaaS documents as the temporary condition to retry.
- BaaS now aggregates providers and stamps each quote with a `platform`,
  while `/swap` routes to Chainflip by default. Quotes from any other
  platform are ignored, so the minimum-price floor always comes from the
  swap that is actually opened.
- The bundled-key disclosure in the app and the README both said the rate
  you're quoted is the rate you get. That is not true on floating-rate
  swaps, which is the flow ChangeNOW's bundled key uses: the quote is an
  estimate and the amount received can move before execution. Both now say
  what is true: SwapDesk adds no fee of its own, and any commission the
  provider pays is already inside the quote shown.
- The README header still read v0.1.28-alpha.
- Follow-up adversarial self-review of the fixes below, specifically hunting
  for anything the first pass introduced: a real network-consistency race in
  Trocador's rewritten `create_swap()`, a misleading status message, an
  unsafe default parameter, and a defensive gap in the network-picker fix
  itself. All four fixed, all traceable to the write-up above:
  - `create_swap()`'s `/new_trade` payload independently recomputed
    `self._net(from_coin)`/`self._net(to_coin)` instead of reusing the exact
    values already sent to `/new_rate`. `self.networks` is reassigned
    wholesale, with no lock, by `fetch_coins()` on a background coin-refresh
    thread (`app.py`'s startup/post-settings-save refresh) -- if that landed
    between the two calls, `/new_trade` could be sent a different network
    than the one `/new_rate` priced the locked-in rate `id` against. Now
    computed once and reused for both calls, which makes the mismatch
    structurally impossible rather than merely unlikely.
  - The QR-withheld card's "this updates automatically" message was shown
    regardless of whether the provider actually supports a later
    reconciliation poll. Only a deferred-echo provider (Chainflip) is ever
    re-checked; a non-deferred provider whose response simply omitted every
    destination-echo field this app knows how to read (possible today for
    Trocador, whose echo field name varies by version/partner) stays
    "unverified" for the rest of the window's life with nothing ever
    re-checking it. The message now says so plainly instead of promising an
    automatic resolution that cannot happen.
  - `_show_deposit`/`_build_deposit_window`'s `destination_check_status`
    parameter defaulted to `"ok"`. The one real caller always passes an
    explicit value, so this was dead code today, but a safety-relevant
    default should fail closed: a future caller that forgets the argument
    should get the cautious "unverified" rendering (QR withheld, warning
    shown), not a silent "independently confirmed". Changed to
    `"unverified"`.
  - `pick_native_network()`'s `expected`-match path didn't `.strip()` the
    live network strings before comparing (two of its three call sites
    already stripped at their own `network_of` callables; SideShift's did
    not). Could only ever cause a false "unavailable" (a whitespace
    mismatch can't accidentally match a different, wrong network), so this
    was an availability gap, not a routing one -- fixed centrally in the
    shared function so every current and future caller gets it uniformly.
- The deposit window's QR code was not withheld while a deferred-echo
  provider (Chainflip) had not yet independently confirmed the deposit
  channel's destination. The copy buttons were correctly gated behind that
  confirmation, but scanning the QR skipped the gate entirely: it was
  scannable the instant the window opened, before this app had any
  independent confirmation the channel actually pointed at the approved
  destination, which is exactly the MITM/provider-bug scenario the
  verification card exists to catch. The QR is now withheld with its own
  explanatory card whenever the destination is unverified, and rendered
  retroactively (`_apply_destination_echo`) the moment a later poll confirms
  a match -- the same way the copy buttons unlock.
- `pick_native_network()`'s "only one network listed -> trust it as native"
  shortcut (shared by ChangeNOW, SideShift and Trocador's coin-catalogue
  refresh) was unsafe: live-checked against ChangeNOW's real `/currencies`,
  FIRO is currently listed with exactly one row and it's the BSC-bridged
  token, not native Firo -- there is no native row at all right now. The
  shortcut would have silently routed a "FIRO" swap onto Binance Smart
  Chain. Separately, SideShift's BTC and ETH rows are now listed under
  several networks named as full chain words ("bitcoin"/"ethereum"/...),
  which never string-match the ticker, so the multi-network fallback
  dropped both coins as "ambiguous" on every routine coin-list refresh.
  `pick_native_network`/`group_native_rows` now take an optional `expected`
  native-network id (from `constants.COINS`, Trocador's `TROCADOR_NETWORK`,
  or -- for SideShift -- the live `mainnet` field its own `/coins` response
  already carries) and check it first: present among the live rows, it wins
  outright; a curated ticker whose expected native network is absent from
  the live rows is now treated as unavailable rather than guessed at.
- StealthEX's current OpenAPI spec documents nine exchange statuses, not the
  eight this app assumed; `expired` is one of them. Without a mapping it
  fell through to Unknown, which isn't terminal, so an expired StealthEX
  exchange polled forever and never told the user it was over. Added.
- Trocador: read directly from `api.trocador.app`'s current docs (behind a
  Cloudflare check, fetched via a real browser) rather than the response
  shapes this integration had assumed:
  - The base URL pointed at `trocador.app/api`, not the documented
    `api.trocador.app` (it worked, apparently via a reverse-proxy alias, but
    carried no support guarantee).
  - `/new_rate` requires the API key unconditionally (a live unauthenticated
    call returns 401 "Missing API key"), so `can_quote_without_config` is
    now `False`: an unconfigured Trocador is skipped from comparison
    instead of shown as an always-failing error card.
  - `/new_trade` documents `provider` and `fixed` as Mandatory parameters;
    neither was ever sent, since the server chooses the winning underlying
    exchange and that exchange's rate type. `create_swap()` now fetches a
    fresh rate first (Trocador's own documented flow) and trades that
    rate's `id`/`provider`/`fixed`, and also sends the documented
    `address_memo`/`refund_memo` fields ('0', matching every coin currently
    in `constants.COINS`, none of which are memo-tagged).
  - `minimum`/`maximum` are documented only on `/coins` (a static per-coin
    figure), never on `/new_rate` or `/new_trade`'s own response -- reading
    them from the trade/rate response meant they were effectively always
    `None`. Now read from the catalogue `fetch_coins()` already parses.
  - `/new_trade`'s response has no expiry field at all (unlike `/trade`'s
    nested `quotes.expiresAt`, a separate endpoint this app doesn't poll
    just to learn it); `Swap.expires_at` is now honestly `None` for
    Trocador instead of read from field names the endpoint never sends.
- FixedFloat's `/ccies` native-coin filter read a `currency` field that
  doesn't exist in FixedFloat's schema (the real field is `coin`), so
  `row.get("currency") or code` always fell back to `code`, and the
  same-value comparison meant to keep only native (non-network-suffixed)
  coins never filtered anything. No live wrong-network path existed today
  (a separate registry cap already limited the practical effect), but the
  safety filter itself was dormant. Fixed to read `coin`.
- THORChain/Maya's per-chain memo-length safety table allowed 1024 bytes for
  ETH/AVAX/BSC with no documented basis, while THORChain's own memo docs
  state a flat 250-byte cap network-wide with UTXO chains' OP_RETURN limits
  called out as an *additional*, tighter constraint on top of that, not a
  higher one for account-model chains -- the same citation the SOL/XMR rows
  already correctly applied. A memo between 250 and 1024 bytes on an
  EVM-sourced swap could have passed the "will it fit" / SAFETY ABORT checks
  and then been silently ignored by THORChain validators. Every chain in the
  table is now capped at 250.
- Chainflip's 403 responses were given the same "(check your API key)" hint
  as 401s. Chainflip's own error-code docs define 403 as `route_disabled` --
  the pair isn't enabled for the account in its broker dashboard, a
  permissions setting, not a bad key -- so the hint now says that instead.
- The shared JSON error-body parser (`base.py`, every provider using an
  `{"error": ..., "message": ...}` shape) preferred a bare `error` string
  over a `message` field even when both were present. Live-tested against
  ChangeNOW: a request rejected with `{"error":"not_valid_params",
  "message":"Currency btcc is not supported"}` showed the opaque code
  instead of the actual reason. `message` (the human-readable explanation)
  is now preferred when present.
- SideShift's `get_quote()` set `Quote.unsupported` when a ticker wasn't in
  its coin list, but not when the `/pair` call itself failed with "deposit/
  settle method is disabled" -- live-confirmed the current, correct outcome
  for several curated coins (XMR, ZEC, FIRO, DCR, ARRR, BEAM, KAS) today.
  That path now sets `unsupported=True` too, so the UI can say "nobody
  offers this pair" instead of the harsher "something's actually broken".
- `send_amount` on ChangeNOW, FixedFloat and Trocador used
  `_dec(field) or amount`, which can't distinguish a provider echoing a
  literal `0` from a missing/unparseable field, since `Decimal("0")` is
  falsy. Changed to an explicit `is not None` check on all three.

**v0.1.28-alpha**: provider outcome reporting: Chainflip quoting and destination
checks, and refund and hold states across SideShift, ChangeNOW, StealthEX and
Trocador

- A Chainflip swap that was refunded in full was shown as Complete ("Done.
  The coins were sent to your address") and written to history that way.
  BaaS has no refunded state: a fill-or-kill refund arrives as `completed`
  with a refund egress and no swap egress. The outcome is now read from the
  egress legs: swap egress only is Complete, refund egress only is Refunded,
  and both is Partially paid. A `completed` status with neither leg is
  reported as Unknown rather than Complete. The payout or refund amount and
  transaction reference are shown under the status in the deposit window.
- A `failed` Chainflip swap stopped polling while its refund was still on
  the way. It now reports the new non-terminal status "Refund in progress"
  until the refund egress has a witness time or transaction reference, and
  Refunded after that. A `failed` swap with no refund egress is still
  Failed.
- The Chainflip estimated receive was `amount * estimatedPrice`. That rate is
  gross of the deposit and payout gas, so the figure was high by both, and
  the provider ranking used it. `egressAmount` is used instead;
  `estimatedPrice` still anchors the minimum-price floor.
- Quote selection fell back to the first row when no regular quote was
  present, which could be a DCA quote, and treated a row with no `type` as
  regular. Only a row with `type` of `regular` is used now.
- The minimum-price tolerance comes from the executed quote's
  `recommendedSlippageTolerancePercent`, never below 2.5%. A quote
  recommending more than 5% is not offered.
- The destination check could not pass at creation, because
  `/status-by-id` answers 404 until BaaS has polled Chainflip for the new
  channel. Creation retries briefly; if the destination is still not
  available the deposit window re-checks it on each status poll. A match
  confirms it; a mismatch removes the QR code, disables every copy button
  and tells the user not to send.
- SideShift's `refund` (queued) and `refunding` (in progress) states both
  mapped to Refunded, which is terminal, so polling stopped and history
  recorded a refund before one existed. A shift with no refund address, where
  SideShift waits for the user to enter one on its order page, was reported as
  already refunded. `refunding` is now Refund in progress, and `refund` is
  Refund in progress when the shift carries a refund address and Needs your
  input when it does not.
- ChangeNOW's `verifying` was reported as "Confirming deposit", which says the
  deposit is waiting on chain confirmations. ChangeNOW documents it as an
  exchange it has stopped and flagged, which stays stopped until the user
  completes the verification it links them to, so it now reports that it needs
  their input, with ChangeNOW's own account of the state shown underneath.
- StealthEX's `verifying` was reported as Unknown, which tells the user the
  exchange cannot be tracked. StealthEX documents it as a verification request
  raised when a wallet is flagged or an exchange looks suspicious, after which
  the exchange is completed or refunded, so it now reports that it needs the
  user's input.
- StealthEX's `failed` was reported as a terminal failure, which stopped
  polling. Their API reference defines the exchange's `refund_address` as
  where the deposit is returned when an exchange fails, and documents
  `refunded` as a separate state that follows, so a failure carrying a refund
  address is now reported as a refund in progress and polling continues to it.
  A failure with no refund address on the exchange reports that it needs the
  user's input, since StealthEX has nowhere to return the deposit to.
- StealthEX's `expired` mapping was removed: it is not one of the eight states
  their API reference documents. An undocumented state reports as Unknown,
  which keeps polling, rather than as an outcome.
- The StealthEX API key was sent as an `api_key` query parameter. It now goes
  in the `X-SX-API-KEY` header, which StealthEX recommends so that keys do not
  end up in request logs.
- StealthEX error responses put the reason in `err.details`, which nothing
  read, so a refusal such as an amount below the minimum was shown as the raw
  body. It now shows the reason.
- Trocador's `failed` and `halted` both mapped to Failed, which is terminal, so
  polling stopped there. Trocador documents both as "contact support" rather
  than as an outcome, and its trade guarantee describes halted trades being
  settled afterwards, so both now report that they need the user's input and
  keep polling.
- The "needs your input" message described one provider's case (a deposit that
  arrived late, short or over) for every provider that used it. It is now
  provider-neutral, and each provider states its own case under the status.
  FixedFloat keeps the wording it had.
- The bundled-key disclosure named providers by title-casing their config
  section key, so it read "Changenow" and "Dex" rather than ChangeNOW and
  0x (DEX), in both Settings and the quote results. The same keys were then
  used to look each provider up in a map keyed by provider name, which never
  matched, so the per-quote commission line in that row could not render at
  all and failed silently. `providers.CONFIG_SECTIONS` states the pairing
  once, beside the factory that binds the two together, and both disclosures
  translate through it.
- Repository layout: README, LICENSE and SECURITY.md now live once, at the
  repo root, where GitHub reads them. The app folder no longer carries a
  second copy of the README to drift out of sync, and `build.py` looks for
  shipped docs in the app folder first and the repo root second, so a package
  still gets all four.
- The per-asset minimum from `/assets` is used as the quote and deposit
  minimum, and a refusal from BaaS shows its reason instead of the raw error
  body, whether the reason arrives as `detail` ("Amount below minimum.
  Min: ...") or as a field in `errors`. Assets that
  `/assets` reports as disabled or missing are not offered, and one-way
  assets are only offered in their direction.

**v0.1.27-alpha**: audit fixes across the deposit window, the remote provider, proxy handling and the secrets store

- The two small `copy` buttons in the deposit window's details card were not
  behind the acknowledgment gate. The large "Copy deposit address" and "Copy
  amount" buttons stay disabled until the memo box and the address-verified
  box are both ticked; the per-row buttons rendered by `_kv()` were created
  live and never registered with `_sync_buttons()`. On any memo route -- XRP,
  XLM, ATOM, BNB, and every THORChain/Maya swap -- the address could be
  copied and funded without the memo card below the fold ever being read,
  which is the loss mode the whole window is built around. `_kv()` now takes
  `gated=` and returns its button, and every value that leaves this window is
  driven by the same gate. The address row's copy button is not created at all
  when the address failed validation.
- A QR code was still rendered for a deposit address that had just failed
  `address_looks_valid()`. The suppression rule tested only for a memo, so the
  red "STOP, DO NOT SEND FUNDS" card was followed immediately by a scannable
  220x220 encoding of the address it was warning about -- the fastest path in
  the window, and the one representation requiring no acknowledgment at all.
  The CLI had this right (`_confirm_deposit_address` refuses); the GUI now
  matches it. QR generation is also wrapped: a `DataOverflowError` used to
  abandon the window half-drawn, after the history row was written and before
  polling started.
- `remote.py` accepted `refund_address` and never sent it, while preflight
  showed two green ticks confirming its format and that it differed from the
  destination. A user who fills that field is usually withdrawing from an
  exchange address they do not control, which is exactly when a refund to the
  sending address is unrecoverable. It is now transmitted. FixedFloat, which
  genuinely cannot take one at `/create`, declares `IGNORES_REFUND_ADDRESS`
  and preflight says so instead of silently green-ticking the field.
- `RemoteSwapDesk.get_status()` returned the server's status string verbatim
  on the assumption that the server had normalised it. A server answering
  `{"status": "Complete"}` therefore forged a TERMINAL status: polling
  stopped, the window said the coins had been sent, and
  `update_history_status()` wrote it to disk permanently. Statuses are now
  validated against the new `KNOWN_STATUSES` set, with anything unrecognised
  becoming Unknown, which is the contract every other provider already
  honoured through `_map_status`. The order id is percent-encoded into the
  poll path rather than interpolated raw, since it comes back out of a plain
  JSON file.
- Provider sessions ran with `trust_env` at its default, so `HTTPS_PROXY` /
  `ALL_PROXY` were merged in at send time without ever appearing in
  `session.proxies` -- which is what the header route pill, `_proxy_in_use()`,
  the DoH gate in `_request` and the proxy attribution in error messages all
  read. An environment proxy silently carried every provider call while the UI
  said "Direct", and the DoH lookup, which runs on a `trust_env=False`
  session, went out over the real IP carrying the hostname of every provider
  about to be contacted. Provider sessions now match the DoH session: the only
  proxy this app uses is the one configured in Settings.
- Run Diagnostics called `session.get` directly, bypassing every DNS
  guarantee in `_request`. A user who had switched on "Secure DNS mode
  (zero-trust: never use your network's DNS)" and then clicked it emitted a
  plaintext lookup to their ISP resolver for every provider host at once. It
  now resolves through the same DoH path, honours secure mode, and no longer
  announces itself with a distinct User-Agent that undid the generic-UA choice
  at the exact moment the app contacts every provider in sequence.
- The settings-save proxy fallback set `privacy.proxy_enabled` -- the
  pre-0.1.25 key. `proxy_mode()` reads `proxy_mode` first, so the rebuilt copy
  still said "on", `configure_proxy` failed again, and `build_providers()`
  raised a SECOND time from inside its own handler. That escaped into Tk's
  callback hook as an "unexpected error" dialog, so the PRIVACY WARNING banner
  never appeared, `self.providers` kept the old set, and the one path that
  exists to warn about running on clearnet was the one path that crashed.
  `app.py`'s equivalent fallback had always used `proxy_mode`; the two copies
  had drifted. Now aligned, and the recovery rebuild has its own handler.
- `_dec()` rejected non-finite values but not absurd exponents.
  `Decimal("1E+999999999")` is finite, so it passed through to the deliberate
  non-exponential "f" format in `fmt()`/`full_str()`, which expands it in
  full: a measured one-billion-character string and about ten seconds of
  frozen GUI thread, from one field of one quote response, reachable from
  SideShift's public keyless `/pair`. Both `_dec()` and `_parse_amount()` now
  bound the exponent, and the two formatters refuse to render past it.
- The deposit memo had none of the guards the deposit address has, despite
  carrying the same stake on the routes that use one. New
  `SwapProvider._checked_memo()` rejects non-strings and control characters
  and normalises blank to None, and every provider that returns a memo goes
  through it.
- Redirects are no longer followed on API calls. `requests` strips
  `Authorization` across a host change but nothing else, so `X-API-KEY`,
  `API-Key`, `x-changenow-api-key`, `x-sideshift-secret` and `X-API-SIGN`
  would all have survived a 302 to another host, and a redirect target escapes
  the DoH pin, which only ever pins the hostname in the URL we were given.
- `_secure_write()` replaces a file by renaming a new one over it, which
  unlinks the old inode without overwriting it, so `_shred_file()` only ever
  scrubbed the current revision and every earlier copy of `config.json`
  survived in free space. Worse, `change_master_password()` never shredded the
  old envelope, which still decrypts under the OLD password: rotating after a
  keylogger scare revoked nothing against anyone able to carve unallocated
  space. Secrets files are now scrubbed in place before the replace. The
  docstring that claimed "nothing on disk is readable without the password"
  was false for every prior revision and has been corrected.
- A failed `set_master_password()` / `change_master_password()` left the
  session key set with no rollback, so the next save silently encrypted
  everything under a password the UI had just said did not take. Both now
  restore the previous session state on failure.
- The entire state machine for "is this install encrypted" was
  `config.enc.exists()`, so deleting one file was indistinguishable from never
  having set a password -- and the setup prompt could be suppressed through
  `security.master_password_prompt_dismissed`, read from the same plaintext
  file an attacker would be editing. An install could be downgraded to
  plaintext with no prompt, no banner and no record, and the user would then
  re-enter live keys into it. A marker file outside the config store now
  records that encryption was chosen, and a startup modal stops rather than
  opening silently into a fresh plaintext config.
- `address_book.json` is the only thing standing between a coin with no
  curated address pattern and preflight's hard failure, and it was loaded with
  no shape validation at all. It is now coerced to the expected structure,
  with anything malformed dropped rather than trusted.
- The SwapDesk API row declares `THIRD_PARTY_ROUTING`, and preflight names the
  configured server URL at the confirm step. In plaintext mode `config.json`
  is an unauthenticated file that selects which host hands back the deposit
  address, and every other check in the gate validates the user's DESTINATION
  rather than where the coins are about to go. Echoing the URL is what makes a
  substituted server visible to the person about to fund it. (Setting a master
  password closes this properly, since `config.enc` is authenticated.)
- The envelope's format version was written but never read, so a future layout
  change would have reported "wrong password" to every older install -- the
  one message that makes a user reach for the destructive reset.
  `parse_header()` now validates it and says what is actually wrong.
- Smaller: the CLI's memo acknowledgment refuses instead of printing a caution
  and returning the swap anyway, and says so plainly when not attached to a
  TTY; a post-creation SAFETY ABORT now carries the order id, since the swap
  exists at the provider by then; `_only_blocker_is_address_book()` identifies
  the blocking check instead of counting failures; the deposit window's expiry
  row is warn-styled with "do not fund after this time" to match the CLI;
  THORChain and Maya report whether the memo budget forced the refund address
  to be dropped, and the deposit window says so; provider-authored JSON error
  text is truncated the way the non-JSON branch already was; failing-request
  URLs are redacted before reaching error strings and `crash.log`, since
  StealthEX and Chainflip authenticate by query parameter; and `build.py`
  purges compiled `keys_baked` residue from `build/` and `__pycache__`, which
  `purge_baked()` never reached.
- Known, not fixed here: a hostile `/coins` response can still re-chain a
  ticker the registry already knows, because `registry_only()` guards the
  display-name merge and not the per-provider network tables. A correct fix
  needs per-provider curated network tables -- `COINS` records SideShift's
  naming, while ChangeNOW and Trocador use different vocabularies -- so it is
  a design change rather than a patch, and a wrong version of it silently
  drops coins from the pickers.

**v0.1.26-alpha**: build system, CI, and error-path fixes

- Destination addresses are validated by checksum, not by a charset-and-length
  pattern. Every Base58Check format carries a 32-bit checksum whose entire
  purpose is to catch a mistyped character, and none of them was being
  verified: a single wrong character in a BTC, LTC, DOGE, DASH, BCH, ZEC, FIRO
  or ARRR address produced a green "format looks like a valid address" PASS on
  the one field that decides where funds land. Measured before the change,
  100% of single-character corruptions were accepted on those coins. They are
  now decoded, checksummed and matched against the version bytes the chain
  actually mints, which is 0%. bech32, CashAddr, hex, XMR and SOL keep their
  existing checks and are unaffected; the shared-version ambiguity warning
  added earlier in this release still fires on `1...` and `3...`.
- Firo addresses beginning `Z` are accepted. The pattern required a leading
  `a`, but Firo's `0x52` version byte does not always encode to one: when the
  payload's high bytes are small the value falls below a Base58 digit boundary
  and the address begins `Z` instead. Roughly 1.2% of valid Firo addresses
  (246 in a 20,000-address sample) were being rejected as malformed, and
  preflight blocked the swap. Validating by version byte removes the
  assumption rather than widening the character class.
- Providers reject a blank or control-character deposit address. The guard was
  `if not addr`, which only catches `None`, `""` and `0`; a response carrying
  `" "`, `"\n"` or `"\x00"` is truthy, and four of the eight providers
  (Trocador, SideShift, ChangeNOW, StealthEX) passed one straight into a Swap.
  The check now lives on `SwapProvider` as `_checked_deposit_address()` and
  every provider calls it, because eight copies of one guard is how four of
  them came to differ. Surrounding whitespace is stripped rather than
  rejected, matching what the destination comparison already does.
- The CLI's retype prompt enforces. `_confirm_deposit_address()` documented
  itself as requiring the last six characters to be retyped before continuing,
  then printed a warning and returned `None` on every path, so
  `guarded_create_swap()` carried on and returned the swap regardless. It now
  returns a verdict the caller acts on, and it runs the same deposit-address
  format check the GUI has always run, which the CLI lacked entirely.
- `keys_baked.py` can no longer reach a build that was not asked to carry
  keys. PyInstaller resolves imports statically and does not evaluate the
  `try: import keys_baked` guard in `bundled_keys.py`, so the module was
  compiled in whenever the file existed, with or without `--with-keys`: a file
  left behind by an interrupted keyed build was baked into the next one while
  the log printed "Building WITHOUT keys". Unkeyed builds now pass
  `--exclude-module keys_baked`, and a stale file is removed before the build
  starts rather than only in the trailing `finally`, which does not run when
  the process is killed.
- Baked credentials are written `0600` and overwritten before deletion.
  `write_text()` created `keys_baked.py` at the umask default, normally
  `0644`, so every local account could read the affiliate keys during a
  build, and
  removal was a bare `unlink()` that left the plaintext recoverable. This is
  the standard `config.py` already applies to the same class of secret.
- DNS failures are classified from the exception chain rather than the message
  text. Two of the three strings matched on are not stable: "Name or service
  not known" is glibc's `gai_strerror` output and is translated under a
  non-English locale, and `NameResolutionError` is a urllib3 implementation
  detail. A German or French user with failing DNS was told to check their
  internet connection and, in Secure DNS mode, lost the message naming which
  resolvers failed. `socket.gaierror` in the chain is locale-independent; the
  string match is kept as a fallback.
- The deposit window shows the exact amount rather than a rounded one. `fmt()`
  quantises to 8 decimals, so on an 18-decimal coin the screen displayed a
  different figure from the one the "Copy amount" button put on the clipboard,
  and anyone typing what they read sent the wrong amount. The history record
  keeps the exact figure too.
- CI covers the above: address checksum rejection and Firo's leading
  character, the provider and CLI deposit-address guards, and the
  `keys_baked` build handling. These are silent, funds-losing failures when
  they regress, which is the case for testing them rather than trusting them.
- `scripts/README.md` added, because opening `scripts/` gave no indication of
  which file to run. The four folders hold eight scripts between them, and
  the two a Windows user needs are in different places: the launcher in
  `scripts/dev/`, the build in `scripts/windows/`. The top-level README
  already documented all of this, but 400 lines in, which is not where
  somebody who has just unzipped the project is looking. The new file is a
  per-platform table for the three questions actually being asked: run it,
  compile it, or work out why it won't start. No script was renamed or
  moved, so every existing path, link and instruction still resolves.
- Legacy Base58 addresses that several supported chains share are now called
  out before a deposit is committed. BTC, LTC and BCH all minted P2SH
  addresses under version byte 0x05, so one `3...` string is a genuinely
  valid address on all three, and BTC and BCH share 0x00 for `1...` the same
  way. Nothing in the encoding says which chain it belongs to, and no regex
  can invent that, so the address stays VALID (it is one) and preflight now
  warns: it names the chains the string collides with and points at the
  format that removes the doubt (bech32 for BTC, `M...`/`ltc1` for LTC,
  CashAddr for BCH). Previously such an address passed silently, which is how
  a deposit lands unrecoverably on the wrong chain. bech32, CashAddr, LTC's
  modern `M...`, and every non-Base58 format are unaffected and warn-free,
  and an address whose checksum doesn't verify never triggers the warning.
  Same treatment the ARRR/ZEC `zs1` collision already had.
- TLS failures and timeouts are no longer reported as generic connection
  errors. `requests.exceptions.SSLError` and `ConnectTimeout` are both
  subclasses of `ConnectionError` (and `ConnectTimeout` is a `Timeout` as
  well), so `providers/base.py` testing `ConnectionError` first swallowed
  both: a corporate TLS interception read as "check your internet
  connection", and a connect timeout never mentioned the timeout. The
  branches are ordered most-specific-first now. `providers/diagnostics.py`
  had the same overlap, reporting connect timeouts as "connection
  blocked/reset" and sending users after a firewall that wasn't there.
- `secretbox.decrypt()` holds its documented contract on two more malformed
  envelopes. A non-numeric scrypt cost parameter reached `_aad()`'s `int()`
  and escaped as a bare `ValueError` rather than `MalformedEnvelope`;
  `unlock()` never saw it because `derive_key()` range-checks first, but
  `config.py` decrypts directly with a cached session key and does not. An
  envelope recording a `length` AES-GCM does not accept (anything but 16, 24
  or 32) produced the same escape from inside the cipher. Both are rejected
  as malformed now, with no key or password material in the message.
- CI checked for 8 providers when there were 9. `providers/thorfork.py`
  supplies two (THORChain and Maya Protocol), so the count had been wrong
  since Maya was added and every run failed on it. The check now compares
  the provider set by name, which says which provider appeared or vanished
  instead of only that the total moved. The GUI smoke test derives its count
  from the same call rather than repeating the number.
- CI compiles on Windows, macOS and Linux. Everything else runs on Linux
  only and none of it ran PyInstaller, so a missing hidden import or a
  platform packaging failure surfaced for the first time during a tagged
  release, where the fix costs a retag. The new job runs the same
  `python build.py` the release runs and checks the binary exists, that
  CHECKSUMS covers it, and that no `keys_baked.py` survived.
- Release archives carry the version: `SwapDesk-<version>-<platform>.zip`
  rather than `SwapDesk-<platform>.zip`. The name is read from VERSION,
  which is what the tag-check step already claimed it did; the names were
  actually matrix literals, so every release published identically named
  assets and a downloaded file recorded nothing about which release it came
  from.
- `scripts/windows/SEE-ERRORS.bat` removed. Its job was keeping the console
  open, and every failure path in `SwapDesk.bat` already pauses.
  `SwapDesk-Debug.bat` does the same job properly, with a real traceback.
- Debug launchers renamed to match the launcher they debug:
  `Run-Debug.bat` to `SwapDesk-Debug.bat`, `run-debug.sh` to
  `swapdesk-debug.sh`. `build.py` generates its own debug launchers into
  `dist/` and two of them had the same names as these, so the tree held two
  different files called `run-debug.sh` and two called `SEE-ERRORS.bat`, one
  operating on source and one on the compiled binary. Every filename in the
  project is now unique.
- `build_windows.bat` checks for tkinter, matching `build_linux.sh` and
  `build_macos.command`. The python.org installer lets Tk be unticked;
  without it PyInstaller has nothing to collect and the failure moved to
  first launch of the .exe.
- `providers/__init__.py` listed five providers in its module docstring and
  claimed one interface over five APIs. There are nine. StealthEX,
  Chainflip, THORChain and Maya Protocol were missing, the module layout
  omitted `stealthex.py`, `chainflip.py` and `torprobe.py`, and the optional
  RemoteSwapDesk row was not mentioned at all.
- README file map lists `providers/torprobe.py`, which it had never
  included.

**v0.1.25-alpha**: proxy/Tor routing options

- The deposit amount the provider states is now checked against the amount
  the user approved, and a swap outside 2% is aborted before any deposit
  window appears. ChangeNOW, FixedFloat and Trocador report the deposit
  figure in their own create-swap response and SwapDesk displays that
  figure rather than the requested one, because providers legitimately
  round it. Nothing compared the two, so a response carrying 1.0 for a 0.01
  request would have told the user to send 100x what they intended. The
  tolerance is far wider than satoshi rounding and far narrower than a
  misplaced decimal point.
- Destination echo comparison strips surrounding whitespace for every
  address type, not just EVM. A provider echoing "addr " was compared
  byte-exact against "addr" and reported as a mismatch, firing a SAFETY
  ABORT on a correct address. Stripping cannot hide tampering: a different
  address is still different once trimmed.

- Proxy setting is three modes instead of a checkbox: Auto (default),
  Always, Never. Auto probes 127.0.0.1:9050 and :9150 with a real SOCKS5
  greeting at startup and routes through whichever answers, so a user with
  Tor already running gets it without configuring anything and a user
  without it sees no failure. Always uses the address field and fails
  closed, which covers any SOCKS5 proxy, not just Tor. Never makes no
  connection of any kind, matching the previous behaviour exactly.
- A header pill shows the routing actually in effect, since Auto is only
  safe if the resolved state is visible. Settings has a "Check for Tor now"
  button reporting the same probe.
- Existing configs migrate on load: proxy_enabled=True becomes "on", not
  "auto". Someone who deliberately turned Tor on must not be silently
  downgraded to a mode that can run direct.
- Quote and swap failures name the proxy when one is carrying the traffic.
  Some providers block Tor exit nodes, so "check your connection" pointed at
  the wrong thing. Only on network-shaped failures, and for quotes only when
  every provider failed, so a provider-side refusal does not send the user to
  the wrong setting.

- PySocks is a pinned dependency and a PyInstaller hidden import. It was
  neither, so SOCKS5/Tor routing could not work in any compiled build: the
  app failed closed with "PySocks isn't installed. Run: pip install
  requests[socks]", which is not something a user of a binary can act on.
  urllib3 imports socks lazily at first use, so static analysis never saw
  it and the module was silently left out of the bundle.
- Saving a socks5:// proxy now warns that hostnames are resolved locally.
  The existing hard refusal only fires in Secure DNS mode; the DNS leak is
  identical without it, and the toggle's own label promises requests go
  through the proxy. A warning rather than a block, since some proxies
  cannot do remote DNS.

**v0.1.24-alpha**: cross-provider audit fixes (Chainflip, StealthEX, THORChain, SwapDesk API, preflight)
- Fixed the swap-status poller dying on an unknown provider name.
  `self.providers[swap.provider]` sat outside the loop's try/except, so a
  KeyError killed the thread before the first poll and the deposit window
  then showed its creation-time status forever, which reads as a stuck swap.
  Resolved with .get() and an explicit Unknown status when the provider is
  missing, which happens when a swap outlives the provider that created it.
- THORChain/Maya refuse an amount that truncates to zero base units. These
  networks normalise to 1e8, so a dust amount became `amount=0` in the quote
  request. Nodes reject that today, but relying on every node to keep doing
  so is not a guarantee worth taking on a funds path.

- **[High] SwapDesk API provider was unconditionally blocked at the
  pre-flight gate.** `_pair_support_state()` had an explicit branch for
  every provider except `RemoteSwapDesk`, which fell through to the
  catch-all `"no"`. That's not a soft warning: `run_preflight()` turns a
  `"no"` into a hard FAIL, so with a SwapDesk API server configured and
  selected, quotes still worked (they don't go through preflight) but
  hitting Create Swap always failed at the gate, in both the GUI and the
  CLI -- there was no code path in this build that could create a swap
  through a remote SwapDesk API server. Fixed by adding a `RemoteSwapDesk`
  branch that returns `"unknown"`, the same treatment Trocador already
  gets, since the remote provider has no local networks/assets table to
  consult by design; the live dry-run quote immediately after the check is
  the real test either way.
- **[High] StealthEX had no chain disambiguation for USDC.** Every other
  multi-network-aware provider in this app (SideShift, ChangeNOW,
  Trocador, FixedFloat, Chainflip) treats "which chain does this ticker
  mean" as a real-funds question and is explicit about it; StealthEX's
  `SYMBOLS` table mapped every curated ticker, USDC included, to a bare
  lowercase string with no network qualifier, and neither `get_quote` nor
  `create_swap` sent a network field anywhere. If StealthEX's own USDC
  listing isn't unambiguously Ethereum-mainnet, that's a wrong-network
  payout risk. Fixed by excluding USDC from `StealthEX.SYMBOLS` and from
  the live `/currency` refresh, matching how `FixedFloat._CODES` already
  excludes it for the identical reason, until StealthEX's real USDC
  network is confirmed.
- **[Medium] Chainflip's mandatory refund address wasn't enforced until
  `create_swap()`.** Chainflip requires a minimum accepted price on every
  swap and needs a refund address to return the deposit if the market
  moves past it; `create_swap()` already refused correctly without one,
  but `run_preflight()` -- whose whole job is to catch exactly this before
  the user commits -- had no check for it, and the Swap tab's refund field
  was labelled "optional" regardless of provider. A user could fill in
  everything else, leave refund blank, pass every pre-flight check and the
  live quote, and only hit the failure after typing the confirm phrase.
  Fixed: added `Chainflip.REQUIRES_REFUND_ADDRESS`, a matching
  `run_preflight()` check (same pattern as `KYC_ON_FLAGGED`), and the
  refund field in the Swap tab now reads "REQUIRED by Chainflip" in red
  when Chainflip is the selected provider.
- **[Medium] Chainflip sends its API key and swap addresses as URL query
  parameters.** Confirmed against Chainflip's own "Starting a Swap" and
  "Asking for a Quote" API reference (docs.chainflip-broker.io) that this
  is a hard constraint of the BaaS API, not an oversight: both endpoints'
  own worked examples send `apiKey`, `destinationAddress` and
  `refundAddress` as query-string parameters, with no documented POST-body
  or header-auth alternative. (This app already treats the same class of
  data -- a credential plus swap addresses -- as sensitive enough to
  require POST for Trocador, specifically because GET params get logged by
  proxies and web servers.) Since there's nothing to switch Chainflip to,
  this is now disclosed rather than left implicit: `Chainflip.
  LOGS_SENSITIVE_DATA_IN_URL` drives a new pre-flight warning shown on
  every Chainflip swap.
- **[Low] THORChain's SOL/XMR memo budget was on an undocumented generic
  fallback.** `MEMO_LIMITS` had explicit, sourced entries for every chain
  except the two newest `ASSET_MAP` additions, SOL and XMR, which silently
  used the generic 250-byte default. Confirmed against THORChain's own
  memo documentation (dev.thorchain.org/concepts/memos.html) that
  THORChain's memo ceiling is a flat 250 bytes network-wide, with the
  80-byte OP_RETURN figure called out separately as an additional,
  UTXO-chain-specific constraint on top of that -- so 250 is the actual
  correct limit for both non-OP_RETURN chains, not an unverified
  placeholder. Added explicit `SOL` and `XMR` entries so that's clear from
  the table itself.
- **[Low] Preflight's settle-vs-refund "must not be identical" check was
  case-insensitive for every coin, not just EVM.** It compared with
  `.strip().lower()` directly instead of the `_addr_equal()` helper this
  same file already defines and uses correctly elsewhere, which scopes
  case-insensitivity to EVM addresses only (base58/bech32 coins are
  case-sensitive). Fail-safe in the direction it was wrong (only ever
  over-blocked), but inconsistent with the file's own stated rule. Fixed
  by routing the comparison through `_addr_equal()`.
- **[Info] CLI `--provider` help text was stale.** Still listed only
  `sideshift | changenow | trocador | fixedfloat | 0x`, though
  `_build_provider()` already resolved `thorchain` / `maya` / `chainflip`
  / `stealthex` / `swapdesk` by name-prefix. Updated to list all of them.

**v0.1.23-alpha**: pre-release internal review

- Fixed the Swap tab's coin dropdown silently mis-handling StealthEX.
  `_provider_tickers()` (the helper that decides which coins the dropdown
  offers, based on which enabled providers can actually route them) only
  recognised coin tables named `networks`/`currencies`/`codes`/`assets`.
  StealthEX stores its table as `self.symbols`, which wasn't in that list,
  so it contributed nothing to the routable set. Harmless today only
  because every other enabled provider already covers the same coins; if
  StealthEX were ever the sole enabled provider, or once its `fetch_coins()`
  narrows `self.symbols` to StealthEX's real live listing, the dropdown
  would have fallen through to its "nothing enabled" fallback and shown
  every curated coin regardless of whether StealthEX could route it,
  surfacing as a failed quote instead of an accurate dropdown. `preflight.py`
  was never affected: its pre-swap safety gate already checks
  `provider.symbols` explicitly for StealthEX, so no swap could have gone
  through on an unrouted pair, only the dropdown was misleading.
- CHANGELOG and README corrected: BEAM was described as shipping WITHOUT a
  curated address pattern and routed through the uncurated-coin
  retype-last-6 confirmation flow. That's not what `config.py` actually
  does: BEAM has a real curated pattern (SBBS hex, 64-67 characters) and
  `has_curated_pattern("BEAM")` returns `True`, so it never touches that
  path. The source comment in `providers/constants.py` that this claim
  traced back to is corrected too.

**v0.1.22-alpha**: new provider + coin selector

- Added StealthEX, and surfaced the caveat that makes it different from
  every other provider here: it is account-free and non-custodial for
  ordinary swaps, but reserves the right to request identity verification
  on swaps it flags, which in practice means large or unusual amounts.
  That contradicts the premise most people are using this app for, so it
  is disclosed twice: a CAN REQUEST KYC badge and a red note in Settings,
  and a pre-flight warning on every StealthEX swap. Pre-flight is the one
  that matters, since it fires while the decision is still free rather
  than after the deposit is on-chain and the payout is held.
  Their "verifying" status maps to Unknown rather than a terminal state:
  the swap is not finished and polling should continue.

- Added four privacy coins: Firo (FIRO), Decred (DCR), Pirate Chain (ARRR)
  and Beam (BEAM). Routed mainly through Trocador, which aggregates the
  smaller exchanges that list them; a provider that doesn't list one returns
  "unsupported" for the pair, which is the intended outcome.
- **BEAM is curated to the SBBS address format only, on purpose.** Beam
  supports several mutually incompatible destination formats (SBBS hex,
  regular, offline, public-offline, max-privacy) with different lengths and
  charsets, and Beam's own docs state exchanges and mining pools require the
  SBBS form for a payout. A pattern loose enough to accept every format would
  also accept addresses a swap could never actually reach, so the pattern
  (64-67 hex characters) is restricted to SBBS and rejects the rest outright,
  rather than waving them through as "unvalidated."
- **Pirate Chain and Zcash share the Sapling `zs1` address format**, so no
  regex can tell an ARRR address from a ZEC one. Anyone holding both is one
  paste away from an unrecoverable cross-chain send, so preflight warns
  explicitly whenever a `zs1` address is used for either coin. FIRO and DCR
  use prefix-plus-base58 patterns with a length range rather than a fixed
  count, since base58 output varies by a character with leading zero bytes.

- Restored CI, release automation and dependency watching under `.github/`.
  CI has no pytest suite to run, so it is built from checks that don't need
  one: byte-compile, import every module, assert 8 providers build with none
  enabled by default, exercise the destination-echo and address-validation
  controls, a full encrypt/lock/unlock round-trip, a headless GUI
  construction under Xvfb, shellcheck, pinned ruff, and a guard that fails
  if a secret or build artifact is ever committed. It also fails when
  VERSION, README and CHANGELOG disagree.
- Release workflow builds all three platforms on a `v*` tag, refuses when the
  tag and VERSION disagree, and attaches keyless signed provenance. The
  binaries are unsigned, so that attestation is the only machine-checkable
  statement of where a download came from:
  `gh attestation verify <archive> --repo swapdesk/swapdesk`.
  Build jobs stay read-only; only the release job gets `contents: write`.
- Every action is pinned to a commit SHA, each verified against upstream
  rather than copied from memory. A tag is a movable pointer, so pinning to
  one lets a retagged action run with repository credentials.
- Restored `ruff.toml`. Without it CI would lint against ruff's defaults
  instead of this project's rule set, which reports the tree's deliberate
  `# noqa` comments as errors.
- Mutable class attributes in the newer providers are `ClassVar`, matching
  the pattern the older ones already used.

- Chainflip's minimum price is now derived from the rate the user approved
  rather than one fetched during create_swap. Re-quoting there moved the
  floor with the market: a price that fell between quoting and confirming
  produced a lower floor, so the swap completed at the worse rate instead
  of tripping the protection and refunding. The approved rate is matched on
  pair and amount (Chainflip prices by size) and expires after 120s, and
  create_swap refuses rather than substituting a fresh price. Also drops
  the swap from three HTTP calls to two.

- Removed the destroy()/mainloop diagnostic logging. It wrote a stack trace
  to crash.log on every normal window close, so a clean exit was
  indistinguishable from a crash and the file grew on each run. The
  exception hooks and faulthandler stay: those only fire on real faults.
- The coin picker no longer keeps a selection the dropdown has dropped.
  Turning off the last provider routing a coin left it selected, showing a
  ticker not in the list and quoting a pair nothing could route.

- Build now excludes modules the app never imports. Analysis of a real
  build showed setuptools accounted for 130 of the 675 modules in the
  archive, dragged in by other packages' metadata handling rather than by
  anything here, plus multiprocessing (this app is threaded, not
  process-parallel) and assorted dev tooling. Result: 675 -> 479 modules
  and 26.7 -> 24.9 MiB, with cryptography's Rust backend and Tcl/Tk
  verified still bundled and the binary verified still running.
- UPX is now explicitly disabled. It shrinks the binary but rewrites the
  executable in a way heuristic antivirus flags, and an unsigned crypto app
  that gets quarantined is worse off than a larger one that runs. Being
  explicit also stops a UPX on the build machine's PATH from silently
  changing the output.

- Added Chainflip as `providers/chainflip.py`, via Broker-as-a-Service.
  Non-custodial and decentralized like THORChain/Maya, but it needs a BaaS
  API key, and the deposit address is a per-swap channel rather than a
  shared vault, so status polling works normally. Routes BTC, ETH, SOL and
  USDC: the multi-network variants Chainflip lists (usdc.arb, usdc.sol,
  eth.arb) are deliberately left out rather than guessed between, and
  `fetch_coins()` refreshes display names only for the same reason.
  A refund address is mandatory: Chainflip enforces a minimum price and
  refunds when the market moves past it, so `create_swap` refuses without
  one instead of silently dropping the protection. The 2.5% floor is
  derived from the quote, and only the instant 'regular' quote is used, not
  the DCA one, which prices better but behaves differently on slippage.
  The swap response does not echo the destination, so the destination check
  is a follow-up status call that reports unverified rather than ok if it
  can't answer yet.
- The coin dropdowns list only coins that enabled AND configured providers
  actually route, and are a plain dropdown rather than a popup window.
- Each provider now has its own Settings section and toggle.
  THORChain and Maya shared a single combined block, which made
  them read as a special case rather than peers of the others.

**v0.1.21-alpha**: release hardening pass


- Added Zcash (ZEC), routed by SideShift, ChangeNOW, Trocador and FixedFloat.
  Not seeded into the THORChain or Maya pool maps: THORChain announced ZEC
  support in 2026 but delayed the rollout after the Orchard disclosure, so
  the live `/pools` refresh decides rather than a guess baked into source.
  Address validation accepts transparent `t1`/`t3`, Sapling `zs1` and unified
  `u1`, since rejecting the shielded forms would push a privacy user toward a
  transparent address. Because no API here exposes whether a provider can pay
  out to a shielded address, pre-flight warns on shielded destinations instead
  of failing them.

- Re-added THORChain and Maya Protocol as `providers/thorfork.py`, ported
  from the pre-0.1.19 implementation into the modular provider layout. Both
  are protocol-level: no account, no API key, no KYC. A quote is a live pool
  simulation; to execute you send the source coin to a protocol vault with
  the returned memo attached. The memo-budget ladder, the on-chain memo size
  refusal, and the memo-field destination check all came back with them,
  since those are what stop a deposit the network can neither pay out nor
  refund. `get_status()` still reports Unknown by design: the vault is shared
  between depositors, so a swap can't be attributed without the user's own
  deposit tx hash, which this app never sees. Of the two, only Maya routes DASH.
- `_addr_in_memo_fields()` is back in `providers/constants.py`. It was
  removed earlier in this cycle as dead code, which it was: nothing had
  called it since the memo providers were dropped in 0.1.19.
- `preflight._pair_support_state()` understands the pool-table providers.
  Without that branch it fell through to the unrecognised-provider default of
  "routes nothing" and blocked every THORChain and Maya swap at the gate.
- **Every provider now defaults to off.** Absence from `enabled_providers`
  means off, so a fresh install queries nobody until the user picks. Reads go
  through `providers.provider_enabled()` rather than a default at each call
  site, so a new provider can't ship enabled by inheriting a stray `True`.
  The Swap tab explains the empty state instead of falling through to "check
  your connection", which would have sent a new user to debug a working
  network.
- Fixed a case-folding bug in the SwapDesk API provider's post-creation
  destination check. `remote.py` compared the server-echoed destination
  against the approved `settle_address` by lowercasing both sides
  unconditionally. That's only correct for EVM (`0x...`) addresses, where
  checksum casing is cosmetic; for base58/bech32 coins (BTC, LTC, XMR,
  DOGE, etc.) case is a real part of the address, per `preflight.py`'s
  `_addr_equal`, which already handles this correctly for the CLI/GUI
  paths. `remote.py` now imports and reuses `_addr_equal` instead of its
  own inline comparison, so the SAFETY ABORT can no longer be silently
  defeated by a same-case-folded-but-different non-EVM address returned
  through a remote SwapDesk API server.
- Removed AdGuard DNS (94.140.14.14) from the DoH resolver list. Its
  shared, multi-service anycast address needs the real hostname
  (`dns.adguard-dns.com`) in TLS SNI/HTTP Host to route to the DoH vhost,
  unlike Cloudflare/Google's dedicated DoH-only IPs; a bare-IP URL against
  it isn't reliable. Fixing that properly means resolving-then-pinning the
  same way `_resolve_via()` already does for provider requests, not a
  one-line resolver-list entry, so it's out for now rather than shipped
  half-working. Existing configs with `"adguard"` saved under
  `dns.providers` are unaffected: unknown provider keys are filtered out
  in `configure_doh_resolvers()`, falling back to the Cloudflare+Google
  default if that empties the list.
- Fixed a loopback bypass in the SwapDesk API URL check. The rule "plain
  `http://` is only allowed to loopback" was enforced by a regex anchored on
  the text after `http://`, which a userinfo section walks straight past:
  in `http://127.0.0.1:8080@evil.com/` the `127.0.0.1:8080` is credentials
  and the request goes to `evil.com`. That URL passed both the Settings
  validator and the transport check, putting the API key, the pair, the
  amount and the destination address on the wire in clear to an
  attacker-chosen host. Both call sites now parse the URL and compare the
  real hostname, and both go through one function in `remote.py` rather
  than two copies of a pattern that could drift.
- Fixed a path that could destroy an encrypted config. When `config.enc`
  decrypted but its contents wouldn't parse, `load_config()` returned
  `DEFAULT_CONFIG`, and the next `save_config()` re-encrypted those empty
  defaults over the file: keys that were still recoverable a moment earlier
  were gone. It now refuses and leaves the file untouched. The old handler
  was also catching the wrong exception types, so a tampered envelope
  escaped it entirely instead of being handled at all.
- `secretbox` now range-checks the scrypt cost parameters it reads out of an
  envelope. They are attacker-controlled and are used before there is a GCM
  tag to verify, so a hand-edited `n` of 2**30 was an out-of-memory kill on
  what should have been a password prompt. A bad-length nonce now raises
  `MalformedEnvelope` as documented instead of a bare `ValueError`.
- Added `scripts/make_release.py`. `CHECKSUMS.sha256` has been documented in
  the README for several releases with nothing in the tree that produced it,
  and the archive rules were prose in CONTRIBUTING that had already been
  missed once (a `SwapDesk.spec` with absolute paths shipped in 0.1.17). The
  script stages, hashes the staged copy, and refuses to zip if `data/`, a
  `.spec`, a credential file or any `.pyc` is about to ship. The refusal
  scans the raw tree: an earlier version of it ran over the already-filtered
  file list, where `data/` and `.spec` had been dropped a step earlier, so
  the check could never actually fire. `.pyc` is in the list because
  compiled bytecode embeds the absolute path of the source it was built
  from, which no grep of the source will ever surface and which travels in
  the archive as opaque bytes.
- Fixed the dependency install being skipped on Linux and macOS after an
  in-place upgrade. `swapdesk.sh` gated the whole install on a `.deps_ok`
  marker that recorded only that some install had happened, so a `.venv`
  left by an older copy satisfied it forever. Anyone who extracted 0.1.20
  over an existing folder therefore ran without `cryptography`, and got the
  "encryption unavailable, rebuild with build.py" dialog for a build that
  was fine. The marker now records a hash of `requirements.txt`, and the
  launcher does one force-reinstall repair pass when verification fails,
  matching what `SwapDesk.bat` already did. `SwapDesk.bat` had the same
  stale-marker hole behind its early jump to launch and gets the same fix.
  Both launchers now import `cryptography` in their verification step;
  leaving it out meant a venv missing it verified clean.
- The GUI can now confirm a destination address for a coin with no built-in
  format check. `preflight.py` hard-fails those unless the address is in the
  address book, and the remediation text named `config.add_to_address_book()`,
  which nothing in the GUI called: every coin outside the curated set was a
  dead end while the dropdowns kept offering hundreds of them after a coin
  refresh. The preflight dialog now offers to save the address when that is
  the only failed check, gated on retyping its last 6 characters, then
  re-runs the full gate rather than assuming it passes.
- An empty destination echo from a provider no longer fires a SAFETY ABORT.
  Only `None` was treated as "no echo to check", so a response carrying
  `"settleAddress": ""` reached the address comparison, failed it, and told
  the user the destination had been altered and to abort a good swap.
  Blank and whitespace-only echoes now normalise to `None` on the `Swap`
  dataclass, so every provider gets the honest "unverified" path.
- `icacls` is now invoked by absolute path. `CreateProcess` searches the
  application directory and the current directory before `System32`, and
  this app is portable, so an `icacls.exe` dropped beside the launcher would
  have run with the user's token on every config save.
- Clipboard auto-clear timers are scoped to the deposit window that armed
  them. Closing any one deposit window cancelled every pending timer on the
  instance, including other open windows', leaving their addresses in the
  clipboard indefinitely.
- A proxy that can't be enabled no longer turns the setting off on disk.
  Both the startup and the settings-save paths wrote `proxy_enabled: False`
  back to the config, so a temporary condition (Tor not started yet) became
  a permanent silent downgrade: the next launch built cleanly, showed no
  banner, and the routing preference was gone. The direct-connection
  fallback now builds from a copy and the stored preference stands, so the
  warning re-appears each launch until it is fixed or turned off deliberately.
- A `config.json` that is readable but not valid JSON is refused instead of
  being replaced. It returned `DEFAULT_CONFIG`, which the next save wrote
  over the top, destroying keys that a one-character fix would have
  recovered. Same handling the encrypted store already got.
- The copy buttons in the deposit window are no longer reachable when the
  provider returned a malformed deposit address. The verification checkbox
  that unlocks them was documented as being offered only for an address that
  passes the format check, but was built unconditionally.
- `diag_changenow.py` reads the config the way the app does and sends its
  requests through the app's provider session. It read `config.json`
  directly, so once `config.enc` became the store it reported "no config,
  run SwapDesk once and save your key first" to users whose key was saved
  and working, and it built bare `requests.get` calls that ignored the proxy
  and DNS settings, putting the API key on clearnet for someone debugging
  their Tor setup.
- FixedFloat's unsupported-coin message reports the live coin table rather
  than the curated seed, which listed eight coins to installs that had
  several hundred.
- `build_linux.sh` installs with `--require-hashes`, matching the macOS and
  Windows build scripts. It was the only build script without it.
- `requirements-build.txt` is now hash-pinned and installed with
  `--require-hashes` everywhere (both build scripts, the Windows batch
  launcher's build path, release.yml and the README's manual route).
  PyInstaller is the dependency that produces the shipped binary, so a
  substituted wheel puts arbitrary code in every release without touching a
  line of this repo, and it was the one thing still installed unverified.
  One file covers all three runners: pip matches whichever artifact it
  downloads against any of the listed hashes, so every wheel for the pinned
  version plus the sdist is listed. The previous comment in that file said a
  per-platform file was unavoidable; it was wrong. The transitive tree
  (`pyinstaller-hooks-contrib`, `altgraph`, `packaging`, `setuptools`, plus
  the platform-gated `macholib`, `pefile` and `pywin32-ctypes`) is listed
  explicitly, since `--require-hashes` refuses anything unpinned including
  what pip resolves itself.
- Python 3.12 is now the single required version everywhere: both launchers
  and all three build scripts. The
  launchers previously accepted 3.9, which was not merely loose but broken:
  `requirements.txt` pins `requests` and `pillow` versions that declare
  `>=3.10`, so a 3.9 interpreter passed the check and then failed at the
  `--require-hashes` install with "no matching distribution", which reads as
  a corrupt download rather than an unsupported Python. The Windows probes
  accepted any 3.x for the same reason and now test the version, preferring
  `py -3.12` by name; the Windows launcher already downloaded 3.12.7 when it
  found nothing, so that path was consistent and the detection path was not.
  The build scripts had no version check at all and now refuse early rather
  than failing partway through a pip resolve that never names the
  interpreter.
- Removed `_addr_in_memo_fields()` and its EVM regex from
  `providers/constants.py`, along with its re-export in `providers/__init__.py`.
  It was left behind when the OP_RETURN memo subsystem went in 0.1.19: no
  caller remained, and it was the only reason that module imported `re`.
- README carries the repository URL. It told the reader three times to make
  sure they had the official repo without ever naming it, and `build.py`
  copies that README in beside every binary.
- `build.py` now writes to `dist/<platform>/` (windows, macos, linux) with a
  matching `build/<platform>/` work tree, instead of everything landing in a
  flat `dist/`. The macOS and Linux binaries are both called `SwapDesk`, so
  collecting three machines' output in one folder meant whichever arrived
  second silently replaced the first. The generated `.spec` moves there too:
  that is the file full of one machine's absolute paths that shipped inside
  0.1.17, and it is now written outside the source tree rather than relying
  on the archive step to catch it. `build_linux.sh` and the release matrix
  follow the new paths.
- `build.py` now produces a packaged application per platform rather than a
  lone binary. Each `dist/<platform>/` gets the binary, README, SECURITY,
  LICENSE, VERSION, a console-attached debug launcher for that OS, and a
  `CHECKSUMS.sha256` covering the lot. The debug launcher is generated, not
  copied from `scripts/`: those bootstrap a venv and pip install from the
  source tree, so next to a binary download every one of them fails on the
  first line.
- Builds now carry OS packaging metadata. Windows binaries get a version
  resource, so Properties > Details is populated instead of blank; macOS
  builds get a bundle identifier instead of one derived from the binary name,
  which collides with any other app called SwapDesk; Linux builds get a
  spec-valid `.desktop` entry. Icons are used per format if present
  (`icon.ico` / `icon.icns` / `icon.png`) and skipped if not.
- The release workflow now attaches one archive per platform
  (`SwapDesk-<version>-<platform>.zip`) instead of a bare binary. Zipping
  preserves the executable bit, which a raw release-asset upload does not: a
  downloaded macOS or Linux binary previously arrived without `+x`.
- Launcher and build scripts are now grouped by platform:
  `scripts/windows/`, `scripts/macos/`, `scripts/linux/`. Root resolution in
  each moved script was updated for the extra directory level, and
  `build_linux.sh` moved out of the repo root into `scripts/linux/`.
- Added `scripts/windows/build_windows.bat` and
  `scripts/macos/build_macos.command`. Only Linux had a build launcher; the
  other two platforms had to be built by hand. Both mirror `build_linux.sh`:
  venv, hash-checked dependency install, pinned PyInstaller, `build.py`, then
  a check that the expected binary actually exists. The macOS one also fails
  early if the chosen python3 has no working tkinter, which otherwise builds
  fine and dies on first launch.
- Added `.gitattributes`. Without it a clone with `core.autocrlf=true`
  rewrites `scripts/linux/swapdesk.sh` with CRLF and the shebang fails with a bad
  interpreter error naming a path that plainly exists.
- Restored `requirements.in`, which `requirements.txt` names as its source
  but which wasn't in the tree, so the lockfile couldn't be regenerated.
  Build toolchain split into `requirements-build.txt` with PyInstaller
  pinned; the release job installed it unpinned.
- Release workflow: the checksum step globbed all of `dist/`, which fails on
  macOS where a windowed build leaves a `SwapDesk.app` directory next to the
  binary, and all three matrix jobs wrote a file called `SHA256SUMS.txt`, so
  the three uploads collided on the release and only the last one survived.
  Checksums are now per-artifact and cover just the binary.
- CI: the GUI smoke test had been hanging until the job timeout on every
  fresh checkout since 0.1.20. A runner has no `data/` folder, which is a
  genuine first run, and a genuine first run opens the modal master-password
  setup dialog and blocks in `wait_window()` with nothing there to click it.
  The workflow now seeds a config before the check and carries a 5-minute
  step timeout as a backstop. Fixed in CI rather than by adding a bypass to
  `_unlock_or_setup_config`, which stands between the user and their secrets
  and shouldn't grow a branch that exists only for automation.
- CI: all GitHub Actions pinned to commit SHAs; `secretbox.py` added to the
  byte-compile list (it was added in 0.1.20 and never listed); ruff pinned
  and configured in `ruff.toml`, which stops a ruff release from reporting
  the tree's deliberate `# noqa` comments as unused; new `test` job.
- Provider links in Settings copied the URL but reported it through
  `status_line`, which lives on the Swap tab, so the confirmation appeared on
  a panel the user was not looking at and the button read as dead. The
  message now goes to `settings_msg`.
- Removed dead code: `COIN_LIST` in `app.py` and `_provider_supports_pair`
  in `preflight.py`, neither referenced anywhere.

**v0.1.20-alpha**: master-password encryption for the local secrets file
- Provider API keys can now be encrypted at rest. Set a master password and
  the config is stored in `data/config.enc` (AES-256-GCM under a
  scrypt-derived key, see `secretbox.py`) instead of plaintext
  `config.json`. This closes audit finding H-1. The scrypt cost parameters
  and salt are bound into the GCM tag as associated data, so a stored
  envelope can't be edited to a cheaper KDF cost and still decrypt.
- Encryption is opt-in and offered on first run. Skipping it keeps the old
  plaintext behaviour for headless/automated use. When a config is
  encrypted, the password is asked for once at startup (GUI) or on the
  terminal (`preflight.py`), and the derived key is held in memory for the
  session so saves don't re-run the key derivation. Settings gained Set,
  Change and Remove controls with a live status line.
- Setting a password shreds the old plaintext `config.json` (best-effort
  overwrite then unlink) once the encrypted copy is written, so keys don't
  linger in two places. Removing a password writes the decrypted copy back
  out first, then shreds the encrypted store, so there's never a moment
  where neither file exists.
- Fixed a plaintext-leak path: saving while the config was encrypted but
  still locked wrote the secrets back to a plaintext `config.json` beside
  the encrypted file, quietly defeating encryption. `save_config` now
  refuses to write plaintext when an encrypted store exists and the session
  is locked, rather than falling through to the plaintext path.
- `cryptography` is now a pinned, hash-locked dependency in
  `requirements.txt`.

**v0.1.19-alpha**: provider set, DNS resolver caching, redraw cost
- The DoH resolver now reuses one pooled connection instead of opening a
  fresh TCP + TLS connection per query. Ten lookups used to open ten
  connections.
- Successful and failed DoH resolutions are both cached (300s / 30s). A
  failing hostname used to re-query every configured resolver, for both A
  and AAAA, on every single request, so a host that DoH couldn't resolve
  paid the full resolver-count x 2 x timeout budget again and again. The
  error text the user sees is unchanged, what's gone is the repeated work
  behind it. Both caches are still cleared whenever the resolver selection
  changes, and both expire, so a migrated provider host or a recovered
  network is picked up without a restart.
- Concurrent lookups of the same hostname collapse into one query via a
  per-host lock, instead of every provider thread issuing its own.
- The history list no longer redraws on every status poll. Polling runs
  every 15s per open swap and usually reports the same status;
  `update_history_field` now reports whether anything actually changed, and
  the redraw is skipped when nothing did. A swap sitting in "waiting for
  deposit" for ten minutes used to rebuild every row in the list forty
  times.
- The status-colour map is a module constant rather than a dict rebuilt on
  every poll and once per history row per redraw.
- SwapDesk ships five provider integrations: Trocador, SideShift, ChangeNOW,
  FixedFloat and 0x. Settings, diagnostics, `preflight.py` and the deposit
  window were reconciled against that list, and the swap-status poller now
  has a single path (order id) rather than branching per provider family.
- The deposit window's memo warning is now driven purely by whether the
  provider returned a memo, instead of also special-casing source chains.
  Memo/tag pairs (XMR payment IDs, XLM/EOS tags) still suppress the QR code
  and still gate the copy buttons behind the acknowledgment checkbox.

**v0.1.18-alpha**: providers split into a package, DNS-over-HTTPS, and the
regression the split caused
- `providers.py` is now `providers/`, one module per integration
  (`sideshift`, `changenow`, `trocador`, `fixedfloat`, `zerox`)
  plus `base.py` for the shared types, `constants.py` for the COINS/status
  vocabulary, `dns_hardening.py` for the resolver, and `diagnostics.py` for
  connectivity checks and `build_providers()`. A 2600-line file meant every
  change to one provider was read in the context of four others.
  `providers/__init__.py` re-exports the old public surface, so existing
  `import providers as prov` call sites did not change.
- **Fixed a crash the split introduced.** `EVM_TOKENS` moved into
  `providers/zerox.py` and was not re-exported, so `preflight`'s
  `_pair_support_state()` raised `AttributeError` on any 0x pair. That is
  inside the pre-flight safety gate, on the real swap path. Hand-maintained
  re-export lists fail this way silently: the package imports fine and the
  `AttributeError` only fires on the one code path that reads the name.
- DNS-over-HTTPS resolution, off by default, configurable in Settings with
  four providers (Cloudflare, Google, Quad9, AdGuard). Resolved addresses
  are checked against private/loopback ranges before connecting, so a
  hostile answer can't point a provider hostname at localhost. Plain mode
  falls back to system DNS and says so in a banner naming the host; secure
  mode fails closed and never touches the local resolver.
- Removed `SwapDesk.spec` from the tree. It carried absolute paths from one
  machine, and `build.py` drives PyInstaller by CLI and never read it.
  `*.spec` was already gitignored, which is why nobody noticed: release
  archives are built from the working tree, not from git, so the ignore
  list never sees them.
- Cleared 42 unused imports across the new provider modules. Each had
  inherited a verbatim copy of the monolith's import block; `zerox.py`
  alone carried twelve status constants it never used.
- Removed 11 em dashes reintroduced by the split, and eight cross-references
  pointing at `providers.py`, a file that no longer exists.
- README corrections. It claimed no independent security review had been
  done, which the 0.1.17 entry contradicts. The open audit finding (H-1
  plaintext config) is now listed in Known limitations instead of only in
  the changelog.
- File modes normalized. Half the tree was 700 and half 644, so git would
  have recorded documentation as executable.
- Walked the Settings > DNS chain end to end against a real config file:
  the resolver checkboxes reach disk and the live module in
  `DOH_PROVIDER_ORDER` order (not click order), an empty selection is
  refused rather than silently reverting to defaults, the resolution cache
  is cleared when the selection changes so a switched-away resolver's
  answer isn't reused, and the whole thing survives a restart.
- **Secure DNS mode plus a `socks5://` proxy is now refused at save time.**
  Any proxy takes the DoH path out of `_request` entirely, because the
  proxy resolves instead. With `socks5h://` that is correct and the promise
  holds. With `socks5://` PySocks resolves client-side, so your network's
  resolver saw every provider hostname while the checkbox said it never
  would. Settings > DNS also now states plainly that these toggles do
  nothing while the proxy is on, instead of appearing to stack with it.
- **Closing the window during a network call no longer prints a traceback.**
  All seven worker-thread callbacks (coin refresh, quote fetch, swap
  creation and its failure path, status polling, diagnostics, the DNS
  fallback banner) went straight to `self.after()`. Closing the app while
  one was in flight raised `RuntimeError`/`TclError` inside the thread,
  where nothing caught it, so it hit stderr; on Windows that could be the
  last thing written before the console disappeared. They now go through
  `SwapDesk._post()`, which drops the callback if the window is gone.

**v0.1.17-alpha**: independent security audit fixes
- M-1: `config.py` gains a public `has_curated_pattern()` helper so callers
  don't reach into `config._PATTERNS` directly to tell curated-format coins
  apart from ones only getting the weak `len(address) >= 16` fallback.
  `preflight.py` now uses it. (The address-book-confirmation requirement for
  uncurated coins itself was already in place.)
- M-2: `providers._resolve_via`'s DoH-fallback resolver no longer patches
  `socket.getaddrinfo` process-wide under a lock held for the whole retried
  HTTP request. It now installs one passthrough wrapper (once, lazily) that
  only ever consults the CALLING THREAD's override map, so concurrent
  threads (status polling, coin refresh, concurrent provider quoting) never
  contend with or block on each other's DNS fallback.
- M-3: the Windows ACL lock-down (`config._lock_down` / `_ensure_dir`) used
  to read only the `USERNAME` env var and silently skip `icacls` (leaving
  inherited, non-owner-only permissions with no warning) if it was empty.
  A new `_windows_user()` also tries `os.getlogin()` and `getpass.getuser()`
  and raises if none resolve, so the failure now surfaces via the existing
  one-time startup warning instead of failing silently.
- M-4: the bundled-provider-key disclosure banner (`config._BUNDLED_IN_USE`)
  used to be append-only for the process lifetime, so it could keep
  claiming a provider was "using the bundled key" after the user entered
  and saved their own, until restart. `_apply_bundled` now recomputes the
  set from scratch each call; a new `config.refresh_bundled_state()` is
  called from the Settings save handler so the banner updates immediately.
- Not yet addressed from the same audit: H-1 (encrypt `config.json` at
  rest; still plaintext + OS file permissions only).

**v0.1.16-alpha**: release archive hygiene
- No code changes from 0.1.15. The release archive itself was carrying
  `__pycache__/` and an empty `data/` directory, neither
  of which belong in a distributed build: the first two are stale
  bytecode cache regenerated automatically on next run, and a
  committed `data/` risks looking like a place to drop `config.json`
  when `config.py` already creates it at runtime.
- Archive now contains source and docs only. No functional change; this
  release exists so the artifact people download matches what the repo
  is meant to look like.
- Cleared 55 ruff lint findings (mutable class-attribute defaults now
  typed `ClassVar`, blind `except Exception` narrowed to
  `contextlib.suppress()` where the failure is genuinely discarded or
  documented with a reason where it isn't, plus assorted style fixes).
  No behavior change.
- `remote.py` was never wired into CI's byte-compile step, so a syntax
  error in it would have shipped undetected, the same gap
  `preflight.py` had before 0.1.9. Added it. This also
  surfaced two real unused imports
  in `remote.py` (`json`, `decimal.Decimal`), now removed.
