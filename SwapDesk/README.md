# SwapDesk

**v0.1.27-alpha** | <https://github.com/swapdesk/swapdesk>

A desktop app for swapping crypto without a custodian. It runs locally on
Windows, macOS and Linux as a native window (no browser, no Electron, no
server to host) and queries nine providers directly: Trocador,
SideShift.ai, FixedFloat, 0x, ChangeNOW, Chainflip, StealthEX, THORChain and
Maya Protocol. Every provider except 0x settles the output coin straight to an
address you supply. SwapDesk
itself never receives, holds or forwards funds; see
[How the non-custodial design works](#how-the-non-custodial-design-works)
for what that does and does not protect you from.

### Alpha status and security disclosure

This is early software, and it handles real swap flows
against real providers. An independent source-code security review of an
earlier release found no critical issues (it predates the Chainflip,
THORChain and Maya providers); a follow-up cross-provider audit of those
four newer providers plus the SwapDesk API remote provider found two
high-severity issues (the SwapDesk API provider was unconditionally
blocked at the pre-flight gate; StealthEX had no chain disambiguation for
USDC) and two medium-severity issues specific to Chainflip (a missing
refund address wasn't caught until swap creation; the BaaS API sends
credentials and addresses as URL query parameters, confirmed against
Chainflip's own API reference to be a hard constraint of that API rather
than a fixable oversight, and is now disclosed in the pre-flight check
rather than left implicit) -- all four are fixed or, for the Chainflip
query-string item, disclosed, see [CHANGELOG.md](docs/CHANGELOG.md).
One high-severity item has been closed: config.json can now be encrypted
at rest with a master password (AES-256-GCM under a scrypt-derived key).
Encryption is recommended and offered on first run; a plaintext store
remains available as an opt-out for headless use. Read
[Known limitations](#known-limitations) before pasting in real API keys or
swapping meaningful amounts. Bug reports welcome; for anything security-sensitive read
[SECURITY.md](docs/SECURITY.md) first.

Typical flow: enable at least one provider in Settings (they all ship off),
pick `BTC -> XMR`, enter an amount and your Monero address, hit Get best
rate, choose a provider, Create swap. You get a deposit address, send your
BTC, and the app polls the swap until the provider delivers. A QR code is
shown unless the swap needs a memo, in which case it is deliberately
suppressed so nobody scans an address without the memo attached. THORChain
and Maya can't be polled at all and report Unknown; see their sections.

See [CHANGELOG.md](docs/CHANGELOG.md) for release history.

## Get it

The only official source is <https://github.com/swapdesk/swapdesk>. Forks
and mirrors are not vetted, and a build from one is not the build these
checksums describe.

- Compiled builds: coming soon <https://github.com/swapdesk/swapdesk/releases>
- From source:

```bash
git clone https://github.com/swapdesk/swapdesk.git
cd swapdesk
```

## Run it

**Python 3.12 or newer**, on every platform, both for running from source
and for building a binary. The launchers check for it and the build scripts
refuse without it. The pinned dependencies in `requirements.txt` declare
`>=3.10`, and the binaries are compiled on 3.12, so 3.12 is the version this
project is actually exercised on. The Windows launcher installs it for you
if it isn't present.

### Windows

1. Put the whole `swapdesk` folder anywhere on your Windows 11 PC.
2. Double-click `scripts\dev\SwapDesk.bat`.
   - SmartScreen will likely warn "Windows protected your PC" since this
     build isn't code-signed (alpha, no cert yet). Click More info -> Run
     anyway if you trust the source. Make sure you got it from the official
     repo, not a fork or mirror, before doing this.

On the first run the launcher will find Python (or download and install it
silently if missing), create a virtual environment, install dependencies
(`customtkinter`, `requests`, `qrcode`, `pillow`, `cryptography`), and launch the app with
no console window.

The venv normally lives at `.venv` inside the folder, but Windows caps most
file APIs at 260 characters, and a venv buries files under
`Lib\site-packages\...`, so a deep install path fails with "the filename or
extension is too long." If your install path is long, the launcher puts the
venv under `%LOCALAPPDATA%\SwapDesk\venv` instead - check the `Venv folder:`
line in `setup_log.txt` to see which one it picked.

No admin rights needed, Python installs per-user, and your keys and history
stay in `data/` inside this folder either way.

### macOS

1. Install Python 3.12+ if you don't have it:
   `brew install python@3.12 python-tk@3.12` (or from
   [python.org](https://www.python.org/downloads/macos/), which bundles Tk).
2. Put the whole `swapdesk` folder anywhere.
3. Double-click `scripts/dev/SwapDesk.command`.
   - First time only: right-click it -> Open (Gatekeeper blocks unsigned
     scripts on a plain double-click - this build isn't notarized, no
     Apple developer cert yet) -> confirm Open. Only do this if you trust
     where you got the download from.
   - If double-clicking still doesn't work, run it from Terminal:
     `./scripts/dev/SwapDesk.command`

### Linux

1. Install Python 3.12+, venv, and Tk, these come from your distro's
   package manager, not pip:
   - Debian/Ubuntu: `sudo apt install python3.12 python3.12-venv python3-tk`
   - Fedora: `sudo dnf install python3.12 python3-tkinter`
   - Arch: `sudo pacman -S python tk`
2. Put the whole `swapdesk` folder anywhere.
3. Run: `./scripts/dev/swapdesk.sh`
   (or double-click it, if your file manager runs executable scripts)

### All platforms

On first run, the launcher:
- creates a virtual environment: `.venv` inside the folder on macOS/Linux,
  and on Windows either `.venv` or `%LOCALAPPDATA%\SwapDesk\venv` if the
  folder path is long enough to hit MAX_PATH,
- installs dependencies (`customtkinter`, `requests`, `qrcode`, `pillow`, `cryptography`)
  from `requirements.txt`, pinned by hash,
- verifies the app imports cleanly,
- launches the app with no attached console window.

Later runs skip straight to launching (a second or two).

## One-time setup: provider keys

**Every provider ships switched off.** A fresh install contacts no swap
service until you enable one in Settings, so nothing is queried and no
affiliate code is attached without a deliberate choice. The Swap tab will
say so rather than looking broken.

Two of them need no signup at all: **THORChain** and **Maya Protocol** are
permissionless liquidity protocols, so there is no account, no API key and
no KYC. Read the memo warning in Settings before enabling them: you deposit
to a shared protocol vault with a memo attached, the memo is what routes
your swap, and a deposit that arrives without it is generally unrecoverable.
For the same reason SwapDesk cannot poll their status afterwards and will
report Unknown rather than guess. Of the two, only Maya routes DASH.


Open the Settings tab and paste your credentials:

| Provider    | What you need                                             | Where |
|-------------|----------------------------------------------------------|-------|
| Trocador (recommended) | one free API key (no KYC), routes across 20+ exchanges | https://trocador.app/en/register/affiliate |
| SideShift   | account secret (`x-sideshift-secret`) plus account ID (`affiliateId`) | https://sideshift.ai/account |
| ChangeNOW   | API key (`x-changenow-api-key`)                      | https://changenow.io/affiliate |
| FixedFloat  | API key plus API secret (the secret signs requests locally via HMAC and is never sent) | https://ff.io/user/apikey |
| 0x (DEX)    | free API key, quote-only (see note below)            | https://dashboard.0x.org/apps |
| Chainflip   | Broker-as-a-Service API key; decentralized, needs a refund address | https://chainflip-broker.io |
| StealthEX   | free API key. **Can request KYC on flagged (large) swaps** | https://stealthex.io/partners/api/ |
| THORChain   | nothing: no account, no key                          | https://thorchain.org |
| Maya Protocol | nothing: no account, no key                        | https://www.mayaprotocol.com |

You only need **one** provider to start, and two of them need no signup at
all: **THORChain and Maya work immediately** with no account, key or KYC.
Read their memo warning first, since a deposit sent without the memo is
generally unrecoverable.

If you want a keyed provider, **Trocador is the best single choice**: it's
an aggregator, so one free key compares FixedFloat, ChangeNOW,
MajesticBank, Godex and others and auto-picks the best rate. The winning
underlying exchange is shown as "via ..." on each quote.

> **0x is different from every other provider here.** It only quotes prices
> between EVM tokens on Ethereum mainnet (today: ETH and USDC). It doesn't
> create a swap through SwapDesk at all. There's no deposit address; you
> take the quoted price as a reference and execute the trade yourself in
> your own wallet (e.g. MetaMask). If you don't do on-chain EVM swaps, you
> can skip 0x entirely.

These are **affiliate/partner keys for creating swaps**. They are *not* wallet
keys and cannot move your coins. They're stored locally in `./data/` (restricted
to your user account where possible): encrypted in `config.enc` if you set a
master password, or in plaintext `config.json` if you don't. Keep the folder
off shared drives.

## Privacy coins beyond Monero

Firo (FIRO), Decred (DCR), Pirate Chain (ARRR) and Beam (BEAM) route mainly
through Trocador, which aggregates the smaller exchanges that list them.

**Pirate Chain and Zcash use the identical Sapling `zs1` address format.**
SwapDesk cannot tell one from the other, so it warns whenever you use a
`zs1` address for either. Check you copied it from the right wallet; a
cross-chain send is unrecoverable.

**Beam only accepts the SBBS address format.** Beam has several mutually
incompatible address formats (SBBS hex, regular, offline, public-offline,
max-privacy), and only SBBS is documented as what exchanges and mining pools
require for a payout. SwapDesk validates against that one format (64-67 hex
characters) and rejects the others outright, rather than accepting a
newer-style address the swap could never actually reach.

## Zcash

Zcash routes through SideShift, ChangeNOW, Trocador and FixedFloat. It is not
seeded for THORChain or Maya: THORChain announced ZEC support in 2026 but the
rollout was delayed, so rather than guess, the pool list is left to the live
`/pools` refresh, which picks it up automatically if and when it is trading.

**Transparent vs shielded.** SwapDesk accepts all three address families as a
destination: transparent `t1`/`t3`, Sapling `zs1`, and unified `u1`. Whether a
given provider can actually pay out to a shielded address is provider- and
route-specific, and this app has no way to ask. Pre-flight warns when the
destination is shielded; confirm on the provider's own site before sending, or
use a t-address if you're unsure. A swap that cannot pay out gets refunded
rather than completed, which costs you the network fees and the time.

## How the non-custodial design works

- **Trocador** is an aggregator: one API key compares FixedFloat, ChangeNOW,
  MajesticBank, Godex and others and picks the best rate, then creates the
  swap through whichever underlying exchange won (shown as "via ..." on the
  quote). Settles directly to your destination address.
- **SideShift**: creates a *variable-rate shift* that settles directly to your
  destination address. Valid 7 days; the rate locks when your deposit is seen
  on-chain.
- **ChangeNOW**: creates a *standard (floating) exchange* that sends the output
  directly to your address.
- **FixedFloat**: creates a fixed- or floating-rate order (API key + secret;
  the secret only signs requests locally via HMAC, it's never transmitted)
  that settles to your destination address. Covers BTC/ETH/XMR/LTC/DOGE/BCH/
  DASH/SOL/ZEC.
- **0x**: fundamentally different from the rest: it's a live price quote
  across on-chain DEXs/AMMs on Ethereum mainnet, not an order. **There's no
  deposit address and SwapDesk cannot create the swap for you**, you take
  the quote as a reference and execute the trade yourself, signed from your
  own wallet (e.g. MetaMask). Only works between EVM tokens on the same
  chain (today: ETH and USDC).

For every provider except 0x, SwapDesk only ever asks for a **deposit
address** (and, on memo/tag coins, a memo) and shows it to you. Your coins
go from your wallet -> the provider/network -> your destination wallet.
SwapDesk is a front end; it is never in the path of your money. For 0x,
SwapDesk isn't in the path at all, it only displays a price, and the trade
itself happens entirely in your own wallet.

## SwapDesk API server (optional)

Since 0.1.15, Settings has a "SwapDesk API server" section. Enter a base
URL and an API key for a self-hosted `swapdesk-api` instance and it joins
the rate comparison as one more row (`remote.py`, `RemoteSwapDesk`),
quoting and creating swaps on its own provider accounts. This lets an
install run with none of its own exchange keys. It's off by default; the
direct-to-provider path described above is unchanged.

This is a genuine tradeoff, not a strict upgrade: a server operator sees
every pair, amount and destination routed through it, all in one place,
where going direct spreads that across each exchange individually. Only
turn it on if you trust the operator (yourself, on your own box, is the
common case).

Two safeguards specific to this path:
- The destination address the server reports back is checked against the
  address you actually approved on screen, before any deposit address is
  displayed. A mismatch aborts rather than warns.
- Settings refuses an `http://` URL to anything other than loopback, since
  the key, pair, amount and destination would otherwise go out
  unencrypted. `http://127.0.0.1` still works for local testing.

## Building a binary

`build.py` compiles SwapDesk into a single double-clickable file, no
Python install, no launcher script, no venv:

Use the build script for your platform. Each one creates the venv, installs
the hash-pinned dependencies and PyInstaller, then runs `build.py`. Each
platform folder holds exactly one file, and that file is the build:

```
Windows    scripts\windows\build_windows.bat      -> dist\windows\SwapDesk.exe
macOS      ./scripts/macos/build_macos.command    -> dist/macos/SwapDesk
Linux      ./scripts/linux/build_linux.sh         -> dist/linux/SwapDesk
```

Nothing else in `scripts/windows/`, `scripts/macos/` or `scripts/linux/` is
a choice you have to make, because there is nothing else in them. The
launchers for running from source without compiling live in `scripts/dev/`.

Add `--with-keys` to bake in the project's affiliate keys. Or call `build.py`
directly if you already have an environment:

```bash
pip install --require-hashes -r requirements-build.txt
python3 build.py
```

Output lands in a per-platform folder under `dist/`, packaged the way a
compiled application ships rather than as a bare binary:

```
dist/linux/
  SwapDesk              the binary
  run-debug.sh          run with the console attached, for a startup failure
  swapdesk.desktop      desktop-environment entry
  README.md  SECURITY.md  LICENSE  VERSION
  CHECKSUMS.sha256      covers everything above
```

Windows gets `SwapDesk.exe` (with a real version resource: company,
description and version show up in the file's Properties > Details, which is
one of the things SmartScreen weighs on an unsigned binary) plus
`SEE-ERRORS.bat`. macOS gets `SwapDesk` and `SwapDesk.app` with a proper
bundle identifier, plus `Run-Debug.command`.

Icons are picked up if present and skipped if not: drop `icon.ico`,
`icon.icns` or `icon.png` next to `build.py`. The repo ships without artwork.

PyInstaller cannot cross-compile, so each platform's binary must be built on
that platform (or in a CI matrix). The folders exist because those three
builds happen on three different machines and then get collected in one
place: the macOS and Linux binaries are both just called `SwapDesk`, so a
flat `dist/` means whichever arrives second silently replaces the first.

`--console` keeps a console window attached, which is how you read a
traceback from a binary that won't start.

**Where your keys and history live in a compiled build.** Not next to the
binary. A one-file PyInstaller build unpacks into a temp directory that is
deleted on exit, so writing there would lose your API keys on every launch,
and the folder beside the `.exe` often isn't writable anyway. Frozen builds
use the platform's per-user location instead:

| Platform | Location |
|---|---|
| Windows | `%APPDATA%\SwapDesk\data\` |
| macOS   | `~/Library/Application Support/SwapDesk/data/` |
| Linux   | `$XDG_DATA_HOME/SwapDesk/data/` (usually `~/.local/share/...`) |

Running from source keeps the old portable behaviour, `./data/` beside
`app.py`: so a source checkout stays self-contained.

### Building a keyed release

Keys are **not** committed. `bundled_keys.py` is tracked and ships empty;
`build.py --with-keys` reads credentials from `keys.local.json` (gitignored)
or from the environment, writes a temporary `keys_baked.py`, compiles it in,
and deletes it again, including if the build fails.

```json
{
  "trocador":  {"api_key": "..."},
  "changenow": {"api_key": "..."},
  "dex":       {"api_key": "..."}
}
```

Or, for CI: `SWAPDESK_TROCADOR_API_KEY`, `SWAPDESK_CHANGENOW_API_KEY`,
`SWAPDESK_DEX_API_KEY`.

So the public source tree is always a bring-your-own-keys build, and only
the released binaries carry credentials.

> **Compiling is not hiding.** A key inside a PyInstaller binary comes back
> out in about a minute, the archive unpacks with `pyinstxtractor` and the
> `.pyc` decompiles, and plain `strings` often finds it without either.
> Treat every baked key as published. That is why FixedFloat's `api_secret`
> and SideShift's `secret` are refused by `build.py` as well as by
> `config.py`: those sign requests, and publishing one hands anyone the
> ability to sign as that account.

## Bundled keys

Some builds ship with the project's own affiliate keys in `bundled_keys.py`,
so the app quotes and creates swaps on first launch with no signup at all.
If yours does, the Settings tab says so next to each affected provider.

**What that means for you.** Swaps created on a bundled key are credited to
the project's affiliate account, which is what funds the work. The rate you
see is the rate you get; the affiliate share comes out of the provider's own
margin, and SwapDesk still never receives, holds, or forwards your coins.
Enter your own key for a provider in Settings and yours is used instead,
a user-entered key always wins, and a bundled one only ever fills a field you
left blank.

**What is never bundled.** FixedFloat's `api_secret` and SideShift's `secret`
sign requests rather than merely identify an account, so publishing one would
let anyone sign as that account. `config.py` refuses to load them from the
bundle regardless of what `bundled_keys.py` contains. Those two providers
always need your own credentials.

Bundled values are held in memory only and are never written to
`data/config.json`, so a project key can't masquerade as yours or outlive a
build that replaced it.

**If you're building your own keyed release:** read the module docstring in
`bundled_keys.py` first. Anything shipped in a downloadable archive is
public (obfuscation buys nothing) and most provider affiliate terms
prohibit redistributing a key, with revocation breaking the app for every
user at once.

## Notes & safety

- The "estimated receive" is indicative (floating rate). The final amount is
  fixed when your deposit confirms, normal for non-custodial swaps.
- If a memo/tag is shown for the deposit, **you must include it** or the deposit
  can be lost. The app flags this in red.
- Set a **refund address** when creating a swap so funds can come back if a
  swap can't complete.
- Address fields get a light format sanity-check before you commit, but always
  double-check the address yourself.
- To add more coins, extend the `COINS` map in `providers/constants.py`
  (ticker -> per-provider network name).

## Files

```
scripts/README.md        which file to run, per platform. Start here if
                           you are looking at scripts/ and want the one
                           file that launches or builds the app
scripts/windows/         build_windows.bat - the only thing you run to
                           compile SwapDesk on Windows
scripts/macos/           build_macos.command - the only thing you run to
                           compile SwapDesk on macOS
scripts/linux/           build_linux.sh - the only thing you run to
                           compile SwapDesk on Linux
scripts/dev/             running from source without compiling. Not needed
                           to build, and not needed by anyone who just wants
                           the application:
  SwapDesk.bat             Windows launcher
  SwapDesk-Debug.bat       same, console attached, for a startup failure
  swapdesk.sh              Linux/macOS launcher
  swapdesk-debug.sh        same, console attached
  SwapDesk.command         macOS Finder shim, hands off to swapdesk.sh
app.py                the GUI: window, tab wiring, startup unlock
theme.py              palette, type scale, shared widget constructors
widgets.py            composite widgets (CoinPicker) built on theme.py
ui/                   one mixin per tab, all bound to the SwapDesk instance
  swap_tab.py            quote form and provider comparison
  deposit.py             deposit window, address display, status polling
  settings_tab.py        credentials, privacy toggles, diagnostics
  history_tab.py         past swaps
providers/            all 9 provider integrations, normalized to one interface
  __init__.py            re-exports the package's public API
  base.py                Quote/Swap/ProviderError types + SwapProvider base class
  constants.py           coin registry, status vocabulary, shared helpers
  dns_hardening.py       DoH resolution + DNS-rebinding protection
  torprobe.py            finds a usable local Tor SOCKS port
  sideshift.py, changenow.py, trocador.py, fixedfloat.py, zerox.py
                         one provider per file
  thorfork.py            THORChain + Maya Protocol (memo-based, no account)
  chainflip.py           Chainflip via Broker-as-a-Service
  stealthex.py           StealthEX (can request KYC on flagged swaps)
  diagnostics.py         connectivity diagnostics, coin refresh, build_providers()
config.py             local config, history, address checks
secretbox.py          AES-256-GCM + scrypt envelope for the encrypted config
preflight.py          pre-flight safety gate (dry-run by default; see below)
bundled_keys.py       optional project affiliate keys shipped with a build
                       (see Bundled keys); empty in a bring-your-own build
remote.py             optional SwapDesk API server client (see SwapDesk API
                       server below); off by default, opt-in per install
build.py              compiles a double-clickable binary via PyInstaller
                       (output in dist/windows|macos|linux)
requirements.txt      dependencies (hash-pinned; do not edit by hand)
requirements-build.txt  build-time only (PyInstaller); hash-pinned, all
                       platforms in one file
VERSION               single source of the version string, read at runtime
                       and by build.py
data/                 created on first run: config.json + history.json (gitignored)
LICENSE               MIT
.github/workflows/ci.yml       compile, imports, safety controls, GUI smoke,
                       shellcheck, lint, committed-secret guard
.github/workflows/release.yml  builds all three platforms on a v* tag,
                       with signed provenance
.github/dependabot.yml watches the hash-pinned deps and action SHAs
ruff.toml             lint rule set, so CI and local runs agree
docs/SECURITY.md      private vulnerability disclosure process
docs/CHANGELOG.md     release history
```

## Run without the launcher scripts (optional)

**Windows**
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

**macOS/Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Known limitations

Alpha software, these are known, not surprises:

- **0x can't create swaps at all**, it's quote-only. You take the price as
  a reference and execute the trade yourself in your own wallet.
- **Secrets can be encrypted at rest** with a master password (AES-256-GCM
  under a scrypt-derived key), which is recommended and offered on first
  run. If you opt out (no master password), keys are stored in plaintext
  protected only by OS file permissions (chmod 600 / icacls), which is a
  "keep other users on this machine out," not a "safe if this machine is
  compromised" guarantee. See `secretbox.py`, `config.py`'s module
  docstring, and [SECURITY.md](docs/SECURITY.md).
- **The master password covers `config.json`, not `history.json`.**
  Encryption at rest applies to the provider credential store. Your local
  swap history is a separate file and stays plaintext either way: it links
  provider, timestamp, deposit address and destination address for every
  swap, and for FixedFloat it also holds the order access token that its
  API needs alongside the order id. On a machine where someone can read
  your user profile, that file is the more revealing of the two. It is
  protected by the same OS file permissions, and
  `auto_clear_history_days` defaults to 90 rather than keep-forever for
  this reason; set it lower in Settings > Privacy, or use **Clear local
  history now**, if the retention matters more than the record.
- **No code signing yet** on any platform. See the SmartScreen/Gatekeeper
  notes above. Only run a build you got from the official source.
- **No third-party security audit yet.** One independent review has been
  done and its findings are fixed (see the 0.1.17 changelog entry), but
  that is a single reviewer, not a funded audit by a firm. One of its
  findings is still open and listed below (H-1, the plaintext config, has
  since been addressed with optional master-password encryption). The
  address-format checks in
  `config.py` are explicitly a paste-error sanity net, not full validation.
  The provider is the authoritative validator.
- **`data/config.json` can now be encrypted** (audit finding H-1,
  addressed). Set a master password and provider keys are stored in
  `data/config.enc` (AES-256-GCM, scrypt-derived key) instead of plaintext;
  the plaintext file is shredded once the encrypted copy exists. If you
  decline encryption, the plaintext `config.json` behaviour remains as an
  opt-out and anyone who can read your user profile can read those keys.
- **Nothing here verifies a live provider's API still matches what the
  code expects.** Response shapes are assumed from each provider's docs and
  from what they returned when the integration was written. A provider can
  rename a field without warning. `preflight.py` is the check that runs
  against the real APIs: its dry-run quote is what confirms a pair actually
  routes before you commit funds.

## Testing status

SideShift, ChangeNOW, Trocador and FixedFloat have each been dogfooded end-to-end against live APIs with real
funds ($20-$50 swaps): quote -> deposit -> provider settlement -> funds
landing at the approved `settle_address`. 0x has not yet had a real-fund
pass (its quote-only/self-execute path).

**Chainflip, THORChain and Maya Protocol have had no real-fund pass at
all.** They were added after the dogfooding above and are verified only
against recorded API shapes, not live swaps. THORChain and Maya are also
the two where a mistake is least recoverable: the deposit carries a
mandatory memo and there is no support desk. Treat them as untested and
start small.

This confirms the happy path works end-to-end on the four providers listed
above. It does **not** cover: larger amounts (fee-rounding, dust-limit, and
precision edge cases behave differently at scale than at $20-$50),
concurrent/parallel swap requests, or failure paths (provider timeout,
mismatched destination, a missing required memo). Treat any amount beyond
dogfooded ranges, and the 0x flow, as unverified against live
infrastructure, use `preflight.py`'s dry-run quote first regardless of
provider.

## Pre-flight safety check

Before sending anything real, `preflight.py` gives you an independent check
in front of `create_swap()`: address-format validation, pair-support check,
address-reuse warning, a **live quote** (dry-run. No order is created), and
only creates a real order if you pass `--execute` and then type the literal
confirmation phrase it prompts for. Run it standalone:

```bash
python3 preflight.py --provider trocador --from BTC --to XMR --amount 0.0001 --settle <your address>
```

Add `--execute` to go further after reviewing the dry-run output. See the
module docstring in `preflight.py` for exactly what it checks and what it
deliberately can't (live-API surprises, clipboard/typo errors, anything
outside this codebase).

## Security

See [SECURITY.md](docs/SECURITY.md) for the private vulnerability disclosure
process. Please don't open a public issue for anything that could put
someone's funds or secrets at risk.

## Verifying this build

`CHECKSUMS.sha256` lists SHA-256 hashes for every file `build.py` puts in
`dist/<platform>/`. It's generated at build time and ships beside the
binary; it is not committed to source, so it can't go stale relative to
whatever you actually built or downloaded. Mitigates
a tampered/fake build (e.g. a fork that silently rewrites `settle_address`
before it reaches `create_swap`) that would otherwise be invisible: a
modified file changes its hash.

```bash
# from inside dist/<platform>/
sha256sum -c CHECKSUMS.sha256      # Linux/macOS
# or on Windows (PowerShell):
Get-Content CHECKSUMS.sha256 | ForEach-Object {
  $hash, $file = $_ -split '  ', 2
  if ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -ne $hash) { "MISMATCH: $file" }
}
```

This only proves the files match what was published in *this* release, it
doesn't prove the release itself wasn't compromised at the source. Only get
SwapDesk from the official repo, and check for a `CHECKSUMS.sha256` mismatch
before running an unfamiliar build.

## Failure modes and mitigations

Beyond what automated tests and `preflight.py` verify (request construction,
address format, live quotes), these are the realistic ways funds get lost
using an app like this, and what does/doesn't help:

| Failure mode | Mitigation | Limits |
|---|---|---|
| Correctly-typed but WRONG address (not a format error) | `preflight.py` address book (`config.add_to_address_book`), warns if `settle_address` hasn't been confirmed before | Only as good as your first confirmation; doesn't prove the address is *yours* |
| Deposit address corrupted on-screen (clipboard/render/QR) | `preflight.py` chunks the deposit address and requires retyping the last 6 characters before proceeding | Only catches on-screen corruption, not a compromised clipboard manager that swaps it after copy |
| Memo/tag forgotten (XMR payment IDs, XLM/EOS tags, etc.) | `preflight.py` prints the memo in a hard-to-miss block and requires typed acknowledgment before returning the swap | Cannot force your *sending* wallet to actually include it, that's outside this app |
| Sent after the deposit window expired | `preflight.py` prints `expires_at`; the app's own UI shows swap status | No active countdown/expiry warning yet if you check back later, get a fresh quote if in doubt |
| Wrong chain/network on your sending wallet | Provider-reported network name shown alongside the deposit address | Cannot inspect or control your wallet's actual broadcast network |
| Tampered/fake build | `CHECKSUMS.sha256`, generated at package time and shipped inside each build (see above) | Proves file integrity, not source trustworthiness, only use the official repo |
| Provider dishonesty / exit scam | Diversify across providers; start with small amounts; read [SECURITY.md](docs/SECURITY.md) | Nothing client-side can verify a provider's actual on-chain behavior in advance |

## Troubleshooting

**Windows: the window flashes and closes instantly.**
Run **`scripts\dev\SwapDesk-Debug.bat`** instead. It runs the app with a visible
console, so any Python traceback stays on screen, and it prints `crash.log` afterwards
if one was written. Also check **`setup_log.txt`** (created in the app folder, the repo root).

Most common causes on Windows 11:
- Only the **Microsoft Store "python" stub** is installed. The launcher now
  detects and skips it and uses the `py` launcher instead; if neither exists it
  downloads real Python.
- Python was installed **without Tcl/Tk**. Reinstall from python.org and keep
  the "tcl/tk and IDLE" component checked.
- The folder is inside **OneDrive** and venv creation is blocked, move it to
  `C:\swapdesk` and retry.
- **The path is too long.** `setup_log.txt` shows pip failing with *"the
  filename or extension is too long"* or venv creation aborting. Windows
  caps most file APIs at 260 characters. Since 0.1.5 the launcher detects a
  deep folder and relocates the venv to `%LOCALAPPDATA%\SwapDesk\venv`
  automatically, check the `Venv folder:` line in `setup_log.txt` to see
  which location it chose. If it still fails, extract the app somewhere
  short like `C:\SwapDesk`.

**Quotes come back with providers missing.**
A line reading **`Skipped (no API key set): ...`** above the results means
those providers were never queried, not that they had no rate. Add the key
in Settings.

**Your API keys vanished after updating.**
Extracting a new build over the old folder overwrites `data/config.json`.
Release archives from 0.1.9 onward exclude `data/` for exactly this reason,
and the build refuses to proceed if it slips in, but back up `data/`
before extracting over an existing install regardless.

**macOS/Linux: nothing happens, or it fails silently.**
Run **`./scripts/dev/swapdesk-debug.sh`** instead. It keeps the terminal open so you can read
the traceback. Also check **`setup_log.txt`** (created in the app folder, the repo root).

Most common causes:
- **Tkinter isn't installed.** It's a separate OS package, not a pip
  package, see the Linux install commands above. `python3 -c "import
  tkinter"` should succeed with no error.
- **macOS Gatekeeper blocks the double-click.** Right-click
  `scripts/dev/SwapDesk.command` -> **Open** the first time, or run it from Terminal.
- **Permission denied.** The scripts need the executable bit:
  `chmod +x scripts/dev/*.sh scripts/dev/*.command scripts/linux/*.sh scripts/macos/*.command`
- **`python3-venv` missing (Debian/Ubuntu).** `sudo apt install python3-venv`
