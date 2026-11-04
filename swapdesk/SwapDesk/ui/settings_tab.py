"""Settings tab: provider credentials, privacy toggles, diagnostics.

Split out of app.py as a mixin: every method runs bound to the SwapDesk
instance (self), so this class is never instantiated on its own."""
from __future__ import annotations

import contextlib
import copy
import re
import threading

import customtkinter as ctk

import config as cfg
import providers as prov

from theme import *  # shared colours, fonts, formatters


class SettingsTabMixin:
    def _check_permission_warning(self):
        """One-time banner if config.py couldn't lock down file permissions
        on the secrets/data directory (see config._log_permission_warning)."""
        warn_path = cfg.APP_DIR / "permission_warning.txt"
        # Best-effort startup banner; a failure here (missing file, unreadable
        # perms, GUI not ready yet) must never block startup, and there's
        # nothing narrower to catch since read_text/unlink/the banner call
        # can each fail in ways the toolkit doesn't document.
        with contextlib.suppress(Exception):
            if warn_path.exists():
                msg = warn_path.read_text(encoding="utf-8", errors="ignore").strip()
                warn_path.unlink(missing_ok=True)
                if msg:
                    self._show_proxy_banner(
                        "SECURITY WARNING: could not restrict file permissions "
                        "on your local secrets storage: " + msg)

    def _build_settings_tab(self):
        t = self.tab_settings
        wrap = ctk.CTkScrollableFrame(t, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        enabled_cfg = self.cfg.get("enabled_providers", {})

        # Trocador (aggregator: recommended)
        tr = self._section(
            wrap, "Trocador", badge="RECOMMENDED", border_color=ACCENT,
            subtitle="One free API key (no KYC) routes across 20+ exchanges: "
                     "FixedFloat, ChangeNOW, MajesticBank, Godex and more, "
                     "and picks the best rate for you. Copy it from your "
                     "Trocador profile after registering.")
        self.tr_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.Trocador.name))
        checkbox(tr, "Include Trocador in rate comparison",
                self.tr_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.tr_key = entry(tr, placeholder_text="Trocador API-Key", show="•")
        self.tr_key.pack(fill="x", padx=18, pady=4)
        self.tr_key.insert(0, self.cfg["trocador"].get("api_key", ""))
        secondary_button(tr, "Copy Trocador signup link",
                         lambda: self._open_site(prov.Trocador.site)
                         ).pack(anchor="w", padx=18, pady=(10, 18))

        # SideShift
        ss = self._section(
            wrap, "SideShift.ai",
            subtitle="Account secret (x-sideshift-secret) + Account ID "
                     "(affiliateId) from your account page.")
        self.ss_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.SideShift.name))
        checkbox(ss, "Include SideShift in rate comparison",
                self.ss_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.ss_secret = entry(ss, placeholder_text="account secret", show="•")
        self.ss_secret.pack(fill="x", padx=18, pady=4)
        self.ss_secret.insert(0, self.cfg["sideshift"].get("secret", ""))
        self.ss_affil = entry(ss, placeholder_text="affiliate / account ID")
        self.ss_affil.pack(fill="x", padx=18, pady=4)
        self.ss_affil.insert(0, self.cfg["sideshift"].get("affiliate_id", ""))
        secondary_button(ss, "Copy SideShift signup link",
                         lambda: self._open_site(prov.SideShift.site)
                         ).pack(anchor="w", padx=18, pady=(10, 18))

        # ChangeNOW
        cn = self._section(
            wrap, "ChangeNOW",
            subtitle="API key (x-changenow-api-key) from your affiliate "
                     "dashboard.")
        self.cn_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.ChangeNow.name))
        checkbox(cn, "Include ChangeNOW in rate comparison",
                self.cn_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.cn_key = entry(cn, placeholder_text="API key", show="•")
        self.cn_key.pack(fill="x", padx=18, pady=4)
        self.cn_key.insert(0, self.cfg["changenow"].get("api_key", ""))
        secondary_button(cn, "Copy ChangeNOW signup link",
                         lambda: self._open_site(prov.ChangeNow.site)
                         ).pack(anchor="w", padx=18, pady=(10, 18))

        # FixedFloat
        ff = self._section(
            wrap, "FixedFloat",
            subtitle="API Key + API Secret from your FixedFloat account's API "
                     "management page. The secret is used only to sign requests "
                     "locally (HMAC) and is never sent. No end-user account is "
                     "needed. Covers BTC/ETH/XMR/LTC/DOGE/BCH/DASH/SOL.")
        self.ff_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.FixedFloat.name))
        checkbox(ff, "Include FixedFloat in rate comparison",
                self.ff_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.ff_key = entry(ff, placeholder_text="API key", show="•")
        self.ff_key.pack(fill="x", padx=18, pady=4)
        self.ff_key.insert(0, self.cfg["fixedfloat"].get("api_key", ""))
        self.ff_secret = entry(ff, placeholder_text="API secret", show="•")
        self.ff_secret.pack(fill="x", padx=18, pady=4)
        self.ff_secret.insert(0, self.cfg["fixedfloat"].get("api_secret", ""))
        secondary_button(ff, "Copy FixedFloat signup link",
                         lambda: self._open_site(prov.FixedFloat.site)
                         ).pack(anchor="w", padx=18, pady=(10, 18))

        # 0x (DEX aggregator)
        dx = self._section(
            wrap, "0x: on-chain DEX", badge="ETHEREUM ONLY",
            badge_fg=NEUTRAL_SOFT, badge_text=MUTED,
            subtitle="Free API key from the 0x dashboard. This routes "
                     "through AMMs on Ethereum mainnet directly. There is "
                     "no deposit address; you sign the trade with your own "
                     "wallet. Only works between EVM tokens (today that's "
                     "ETH only, until more ERC-20s are added to the coin "
                     "list), so it won't quote XMR/BTC/LTC/etc.")
        self.dx_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.ZeroExDEX.name))
        checkbox(dx, "Include 0x in rate comparison",
                self.dx_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.dx_key = entry(dx, placeholder_text="0x API key", show="•")
        self.dx_key.pack(fill="x", padx=18, pady=4)
        self.dx_key.insert(0, self.cfg["dex"].get("api_key", ""))
        secondary_button(dx, "Copy 0x signup link",
                         lambda: self._open_site(prov.ZeroExDEX.site)
                         ).pack(anchor="w", padx=18, pady=(10, 18))

        # StealthEX. Badged differently from the others on purpose: every
        # provider above is unconditionally no-KYC, and this one is not.
        sx = self._section(
            wrap, "StealthEX", badge="CAN REQUEST KYC",
            badge_fg=BAD_SOFT, badge_text=BAD,
            subtitle="Account-free and non-custodial for ordinary swaps, but "
                     "StealthEX reserves the right to request identity "
                     "verification on swaps it flags, which in practice "
                     "means large or unusual amounts.")
        ctk.CTkLabel(
            sx,
            text=("If a swap is flagged the payout is held until you verify "
                  "or agree a refund, and your deposit is already on-chain by "
                  "then. Pre-flight repeats this warning before every "
                  "StealthEX swap. Keep amounts modest, or use one of the "
                  "providers above if handing over documents is not "
                  "acceptable to you."),
            text_color=ACCENT, font=F(12), wraplength=520,
            justify="left").pack(anchor="w", padx=18, pady=(4, 8))
        self.sx_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.StealthEX.name))
        checkbox(sx, "Include StealthEX in rate comparison",
                self.sx_enabled_var).pack(anchor="w", padx=18, pady=(0, 8))
        self.sx_key = entry(sx, placeholder_text="StealthEX API key", show="\u2022")
        self.sx_key.pack(fill="x", padx=18, pady=4)
        self.sx_key.insert(0, self.cfg["stealthex"].get("api_key", ""))
        secondary_button(sx, "Copy StealthEX API signup link",
                         lambda: self._open_site(prov.StealthEX.site)
                         ).pack(anchor="w", padx=18, pady=(4, 18))

        # Chainflip (Broker-as-a-Service)
        cf = self._section(
            wrap, "Chainflip", badge="DECENTRALIZED",
            badge_fg=NEUTRAL_SOFT, badge_text=MUTED,
            subtitle="Native cross-chain swaps run by Chainflip's validator "
                     "set, reached through a Broker-as-a-Service account. "
                     "Non-custodial, but unlike THORChain/Maya it needs a "
                     "BaaS API key. Routes BTC, ETH, SOL and USDC.")
        self.cf_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.Chainflip.name))
        checkbox(cf, "Include Chainflip in rate comparison",
                self.cf_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        self.cf_key = entry(cf, placeholder_text="BaaS API key", show="\u2022")
        self.cf_key.pack(fill="x", padx=18, pady=4)
        self.cf_key.insert(0, self.cfg["chainflip"].get("api_key", ""))
        ctk.CTkLabel(
            cf,
            text=("A refund address is required for every Chainflip swap. "
                  "The protocol enforces a minimum price and returns your "
                  "deposit if the market moves past it, so there has to be "
                  "somewhere to send it back to."),
            text_color=ACCENT, font=F(12), wraplength=520,
            justify="left").pack(anchor="w", padx=18, pady=(4, 6))
        secondary_button(cf, "Copy Chainflip BaaS signup link",
                         lambda: self._open_site(prov.Chainflip.site)
                         ).pack(anchor="w", padx=18, pady=(4, 18))

        # THORChain: its own section, same shape as the providers above, so
        # it reads as a peer rather than a special case. No key entry
        # because the protocol has no accounts at all.
        th = self._section(
            wrap, "THORChain", badge="NO ACCOUNT NEEDED",
            badge_fg=NEUTRAL_SOFT, badge_text=MUTED,
            subtitle="Permissionless liquidity protocol, not a company: no "
                     "signup, no API key, no KYC. You deposit to a shared "
                     "vault with a MEMO attached and the network routes it, "
                     "so there is no order to track and status shows Unknown. "
                     "Routes BTC, ETH, LTC, DOGE, BCH, SOL and XMR.")
        self.thor_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.THORChain.name))
        checkbox(th, "Include THORChain in rate comparison",
                self.thor_enabled_var).pack(anchor="w", padx=18, pady=(4, 8))
        ctk.CTkLabel(
            th,
            text=("The memo is mandatory. A deposit sent without it, or with "
                  "it truncated, is generally unrecoverable and there is no "
                  "support desk to contact. Send from a wallet that can "
                  "attach a memo/OP_RETURN, never from an exchange account."),
            text_color=ACCENT, font=F(12), wraplength=520,
            justify="left").pack(anchor="w", padx=18, pady=(0, 18))

        # Maya Protocol
        my = self._section(
            wrap, "Maya Protocol", badge="NO ACCOUNT NEEDED",
            badge_fg=NEUTRAL_SOFT, badge_text=MUTED,
            subtitle="A THORChain fork with the same deposit-with-memo "
                     "mechanics and the same memo warning. Routes BTC, ETH "
                     "and DASH; it is the only DASH route of the two.")
        self.maya_enabled_var = ctk.BooleanVar(
            value=prov.provider_enabled(enabled_cfg, prov.MayaProtocol.name))
        checkbox(my, "Include Maya Protocol in rate comparison",
                self.maya_enabled_var).pack(anchor="w", padx=18, pady=(4, 18))

        # Privacy
        pv = self._section(wrap, "Privacy")
        priv = self.cfg.get("privacy", {})
        # Three modes rather than a checkbox: "use Tor if it's there" and
        # "refuse to run without it" are different promises, and collapsing
        # them into one control forces every user to accept whichever
        # promise the default picked for them.
        self.proxy_mode_var = ctk.StringVar(
            value=prov.proxy_mode(priv))
        modes = [
            ("auto", "Use Tor if it's running on this machine (recommended). "
                     "Runs on a direct connection when it isn't."),
            ("on", "Always use the SOCKS5 proxy below. Refuses to connect if "
                   "it's unavailable, so traffic never falls back to direct."),
            ("off", "No proxy. Direct connection."),
        ]
        for value, label in modes:
            ctk.CTkRadioButton(pv, text=label, variable=self.proxy_mode_var,
                               value=value, text_color=TEXT, font=F(12)
                               ).pack(anchor="w", padx=18, pady=(4, 0))

        # Live result of the same probe the app runs at startup, so the
        # setting can be checked here instead of by launching and guessing.
        self.proxy_detect_lbl = ctk.CTkLabel(
            pv, text="", text_color=FAINT, wraplength=780,
            justify="left", font=F(11))
        self.proxy_detect_lbl.pack(anchor="w", padx=18, pady=(6, 0))
        secondary_button(pv, "Check for Tor now", self._probe_tor,
                         height=30, font=F(11)
                         ).pack(anchor="w", padx=18, pady=(6, 4))
        self._probe_tor()

        self.proxy_url = entry(
            pv, placeholder_text="socks5h://127.0.0.1:9050  (Tor default)")
        self.proxy_url.pack(fill="x", padx=18, pady=4)
        self.proxy_url.insert(0, priv.get("proxy_url", "socks5h://127.0.0.1:9050"))
        ctk.CTkLabel(pv, text="The address above is only used by \"Always\": "
                             "point it at Tor on a non-default port, or at "
                             "any other SOCKS5 proxy you run. Use socks5h:// "
                             "(not socks5://) so DNS lookups go through the "
                             "proxy instead of leaking locally.",
                     text_color=FAINT, wraplength=780, justify="left",
                     font=F(11)).pack(anchor="w", padx=18, pady=(4, 12))

        ctk.CTkLabel(pv, text="Auto-clear local swap history after (days, 0 = never):",
                     text_color=MUTED, font=F(12)).pack(anchor="w", padx=18, pady=(4, 0))
        self.history_days = entry(pv, placeholder_text="90", width=100, height=34)
        self.history_days.pack(anchor="w", padx=18, pady=(6, 12))
        self.history_days.insert(0, str(priv.get("auto_clear_history_days", 0)))

        self.warn_reuse_var = ctk.BooleanVar(
            value=priv.get("warn_on_address_reuse", True))
        checkbox(pv, "Warn if a destination address was used in a previous "
                    "swap (reuse links transactions together on transparent "
                    "chains)", self.warn_reuse_var
                ).pack(anchor="w", padx=18, pady=(0, 8))

        secondary_button(pv, "Clear local history now", self.on_clear_history
                         ).pack(anchor="w", padx=18, pady=(4, 18))

        # Master password (encryption at rest for the secrets file)
        mp = self._section(
            wrap, "Master password",
            subtitle="Encrypt the provider API keys stored on this machine so "
                     "they can't be read off disk without a password "
                     "(AES-256-GCM with a scrypt-derived key). These are "
                     "affiliate keys, not wallet keys, so forgetting the "
                     "password means re-entering keys, never lost funds.")
        self._mpw_status = ctk.CTkLabel(mp, text="", font=F(12, "bold"),
                                        wraplength=780, justify="left")
        self._mpw_status.pack(anchor="w", padx=18, pady=(0, 10))
        if not cfg.crypto_available():
            ctk.CTkLabel(
                mp, text="The 'cryptography' package isn't installed in this "
                         "environment, so encryption is unavailable. Install "
                         "it (it's pinned in requirements.txt) to enable it.",
                text_color=FAINT, wraplength=780, justify="left",
                font=F(11)).pack(anchor="w", padx=18, pady=(0, 18))
        else:
            mp_btns = ctk.CTkFrame(mp, fg_color="transparent")
            mp_btns.pack(fill="x", padx=18, pady=(0, 18))
            self._mpw_set_btn = primary_button(
                mp_btns, "Set a master password",
                self.on_set_master_password, height=38)
            self._mpw_set_btn.pack(side="left", padx=(0, 8))
            self._mpw_change_btn = secondary_button(
                mp_btns, "Change password", self.on_change_master_password)
            self._mpw_change_btn.pack(side="left", padx=(0, 8))
            self._mpw_remove_btn = secondary_button(
                mp_btns, "Remove encryption", self.on_remove_master_password)
            self._mpw_remove_btn.pack(side="left")
        self._refresh_mpw_section()

        # DNS
        dns = self._section(
            wrap, "DNS",
            subtitle="SwapDesk resolves provider hostnames itself over "
                     "DNS-over-HTTPS, against a resolver reached by IP, "
                     "before it ever asks your OS or router. That keeps an "
                     "ISP or home router that fails (or lies) on "
                     "crypto-infra domains out of the path entirely. If "
                     "every resolver below fails, what happens next depends "
                     "on Secure DNS mode.")
        dns_cfg = self.cfg.get("dns", {})
        self.dns_enabled_var = ctk.BooleanVar(
            value=bool(dns_cfg.get("enabled", True)))
        checkbox(dns, "Resolve provider hostnames over DNS-over-HTTPS",
                 self.dns_enabled_var
                ).pack(anchor="w", padx=18, pady=(4, 10))

        self.dns_secure_mode_var = ctk.BooleanVar(
            value=bool(dns_cfg.get("secure_mode", False)))
        checkbox(dns, "Secure DNS mode (zero-trust: never use your "
                      "network's DNS)", self.dns_secure_mode_var
                ).pack(anchor="w", padx=18, pady=(0, 4))
        ctk.CTkLabel(
            dns, text="When ON, every lookup goes through DNS-over-HTTPS "
                      "only. If every DoH resolver below fails, the request "
                      "stops with a clear error instead of quietly falling "
                      "back to your network's DNS. Turn this off and "
                      "SwapDesk instead falls back gracefully, telling you "
                      "when it does.",
            text_color=FAINT, font=F(11), wraplength=780, justify="left"
        ).pack(anchor="w", padx=18, pady=(0, 12))

        ctk.CTkLabel(dns, text="DNS providers (tried in this order until one resolves):",
                     text_color=MUTED, font=F(12)).pack(anchor="w", padx=18, pady=(0, 6))
        chosen = set(dns_cfg.get("providers", prov.DEFAULT_DOH_PROVIDERS))
        toggles = ctk.CTkFrame(dns, fg_color="transparent")
        toggles.pack(fill="x", padx=18, pady=(0, 4))
        self.dns_provider_vars = {}
        for key in prov.DOH_PROVIDER_ORDER:
            meta = prov.DOH_PROVIDERS[key]
            var = ctk.BooleanVar(value=key in chosen)
            self.dns_provider_vars[key] = var
            checkbox(toggles, f"{meta['label']} ({meta['ip']})", var
                    ).pack(anchor="w", pady=3)
        ctk.CTkLabel(dns, text="At least one provider must stay selected while "
                             "DNS-over-HTTPS resolution is enabled.",
                     text_color=FAINT, font=F(11)
                     ).pack(anchor="w", padx=18, pady=(2, 6))
        # These settings do not stack, and silently doing nothing is the
        # worst way to say so. With a proxy on, requests never reaches the
        # DoH path at all: the proxy resolves. That is fine (better, with
        # socks5h) but it means the toggles above stop having any effect,
        # and a user who set both would otherwise assume they had two
        # layers rather than one.
        ctk.CTkLabel(dns, text="Note: when the SOCKS5 proxy above is on, "
                             "these settings do nothing. The proxy resolves "
                             "hostnames instead, which with socks5:// is "
                             "still your network's resolver.",
                     text_color=FAINT, wraplength=780, justify="left",
                     font=F(11)).pack(anchor="w", padx=18, pady=(0, 18))

        # Connection diagnostics
        dg = self._section(
            wrap, "Connection Diagnostics",
            subtitle="If a provider keeps failing with a network error, run "
                     "this to see exactly which hosts are unreachable and "
                     "why (DNS, connection blocked, timeout, TLS), instead "
                     "of guessing at firewall/antivirus settings.")
        self.diag_btn = secondary_button(dg, "Run Diagnostics", self.on_run_diagnostics)
        self.diag_btn.pack(anchor="w", padx=18, pady=(0, 10))
        self.diag_summary = ctk.CTkLabel(dg, text="", text_color=MUTED,
                                         wraplength=780, justify="left", font=F(12))
        self.diag_summary.pack(anchor="w", padx=18, pady=(0, 6))
        self.diag_results = ctk.CTkFrame(dg, fg_color="transparent")
        self.diag_results.pack(fill="x", padx=18, pady=(0, 18))

        # options
        opt = card(wrap)
        opt.pack(fill="x", pady=8, padx=4)
        self.auto_var = ctk.BooleanVar(value=self.cfg.get("auto_select_best", True))
        checkbox(opt, "Auto-select the best-rate provider", self.auto_var
                ).pack(anchor="w", padx=18, pady=18)

        primary_button(wrap, "Save settings", self.on_save_settings, height=44
                       ).pack(fill="x", padx=4, pady=12)
        self.settings_msg = ctk.CTkLabel(wrap, text="", text_color=GOOD, font=F(12))
        self.settings_msg.pack()

        # Report the real path: a frozen build stores config under the
        # per-user data dir, not ./data next to the binary. Text is refreshed
        # by _refresh_mpw_section() so it tracks the encrypted/plaintext state.
        self._storage_note = ctk.CTkLabel(
            wrap, text="", text_color=FAINT, wraplength=780, justify="left",
            font=F(11))
        self._storage_note.pack(anchor="w", padx=6, pady=(8, 14))
        self._refresh_mpw_section()

    @staticmethod
    def _validate_no_control_chars(s: str, field: str) -> str:
        s = (s or "").strip()
        if re.search(r'[\r\n\0]', s):
            raise ValueError(f"{field} contains invalid control characters")
        return s

    def _probe_tor(self):
        """Report whether a local SOCKS proxy is reachable right now.

        Says "SOCKS proxy" rather than "Tor": the probe confirms the
        protocol, not who is speaking it, and claiming Tor on the strength
        of a port number would be a promise this cannot keep.
        """
        if not prov.socks_available():
            self.proxy_detect_lbl.configure(
                text="SOCKS support is missing from this build, so no proxy "
                     "can be used. Reinstall or rebuild with PySocks.",
                text_color=BAD)
            return
        port = prov.find_socks_port()
        if port is None:
            self.proxy_detect_lbl.configure(
                text="No local SOCKS proxy found on 9050 or 9150. Start Tor "
                     "(or Tor Browser) and check again; on \"Auto\" the app "
                     "runs on a direct connection until one appears.",
                text_color=FAINT)
        else:
            self.proxy_detect_lbl.configure(
                text=f"SOCKS proxy detected on port {port}. On \"Auto\" "
                     f"requests will be routed through it.",
                text_color=GOOD)

    def on_save_settings(self):
        # Validated into locals, not straight into self.cfg: a later field
        # failing validation (the proxy URL, the DNS/
        # proxy contradiction check, all further down this same try) used to
        # leave self.cfg holding some newly-typed credential values and some
        # stale ones, in memory, even though the ValueError below means
        # nothing gets to cfg.save_config() at all this call. Harmless today
        # (the next successful save overwrites it, and nothing reads the
        # half-updated state in between), but a validation failure should
        # leave self.cfg exactly as it was, not silently mutated. Applied to
        # self.cfg together, after every check below has already passed.
        try:
            tr_key = self._validate_no_control_chars(
                self.tr_key.get(), "Trocador API key")
            ss_secret = self._validate_no_control_chars(
                self.ss_secret.get(), "SideShift secret")
            ss_affil = self._validate_no_control_chars(
                self.ss_affil.get(), "SideShift affiliate ID")
            cn_key = self._validate_no_control_chars(
                self.cn_key.get(), "ChangeNOW API key")
            cf_key = self._validate_no_control_chars(
                self.cf_key.get(), "Chainflip BaaS API key")
            sx_key = self._validate_no_control_chars(
                self.sx_key.get(), "StealthEX API key")
            ff_key = self._validate_no_control_chars(
                self.ff_key.get(), "FixedFloat API key")
            ff_secret = self._validate_no_control_chars(
                self.ff_secret.get(), "FixedFloat API secret")
            dx_key = self._validate_no_control_chars(
                self.dx_key.get(), "DEX API key")

            proxy_url = self._validate_no_control_chars(
                self.proxy_url.get(), "Proxy URL")
            if proxy_url and not re.match(r'^(socks5h?|https?)://', proxy_url):
                raise ValueError(
                    "Proxy URL must start with socks5h://, socks5://, "
                    "http://, or https://")
            # "On" means the proxy is required. Saving it with no usable URL
            # would only rebuild the providers on a direct connection.
            if self.proxy_mode_var.get() == "on":
                problem = prov.proxy_url_problem(proxy_url)
                if problem:
                    raise ValueError(problem)

            dns_providers = [k for k in prov.DOH_PROVIDER_ORDER
                             if self.dns_provider_vars[k].get()]
            dns_secure_mode = bool(self.dns_secure_mode_var.get())
            if (self.dns_enabled_var.get() or dns_secure_mode) and not dns_providers:
                raise ValueError(
                    "Select at least one DNS provider, or turn off both "
                    "DNS-over-HTTPS resolution and Secure DNS mode.")

            # A proxy takes DNS out of this app's hands entirely: requests
            # resolves through the proxy, so the DoH path in
            # SwapProvider._request is skipped and Secure DNS mode stops
            # applying. With socks5h:// that is the correct outcome, the
            # proxy (Tor) resolves remotely and your network's resolver is
            # still never used. With socks5:// it is not: PySocks resolves
            # the hostname locally and sends the proxy an IP, so every
            # provider hostname goes through your network's resolver in
            # plaintext while the checkbox above promises it never will.
            # The two settings contradict each other, so refuse the save
            # rather than showing a zero-trust label over a DNS leak.
            # Only "on" uses the URL field, so only "on" can leak through a
            # socks5:// typed into it. "auto" builds its own socks5h:// URL.
            if (dns_secure_mode and self.proxy_mode_var.get() == "on"
                    and proxy_url.startswith("socks5://")):
                raise ValueError(
                    "Secure DNS mode and a socks5:// proxy contradict each "
                    "other: socks5:// resolves hostnames locally, so your "
                    "network's DNS resolver would see every provider "
                    "hostname despite Secure DNS mode being on. Use "
                    "socks5h:// (the h means the proxy does the DNS), or "
                    "turn off Secure DNS mode.")
        except ValueError as e:
            self.settings_msg.configure(text=str(e))
            return

        # Every field above validated cleanly; commit them together so a
        # validation failure earlier in the try block (which returns before
        # reaching here) can never leave self.cfg holding a mix of new and
        # stale credential values.
        self.cfg["trocador"]["api_key"] = tr_key
        self.cfg["sideshift"]["secret"] = ss_secret
        self.cfg["sideshift"]["affiliate_id"] = ss_affil
        self.cfg["changenow"]["api_key"] = cn_key
        self.cfg["chainflip"]["api_key"] = cf_key
        self.cfg["stealthex"]["api_key"] = sx_key
        self.cfg["fixedfloat"]["api_key"] = ff_key
        self.cfg["fixedfloat"]["api_secret"] = ff_secret
        self.cfg["dex"]["api_key"] = dx_key

        self.cfg["auto_select_best"] = bool(self.auto_var.get())
        self.cfg["enabled_providers"] = {
            prov.Trocador.name: bool(self.tr_enabled_var.get()),
            prov.SideShift.name: bool(self.ss_enabled_var.get()),
            prov.ChangeNow.name: bool(self.cn_enabled_var.get()),
            prov.FixedFloat.name: bool(self.ff_enabled_var.get()),
            prov.ZeroExDEX.name: bool(self.dx_enabled_var.get()),
            prov.THORChain.name: bool(self.thor_enabled_var.get()),
            prov.MayaProtocol.name: bool(self.maya_enabled_var.get()),
            prov.Chainflip.name: bool(self.cf_enabled_var.get()),
            prov.StealthEX.name: bool(self.sx_enabled_var.get()),
        }

        try:
            days = int(self.history_days.get().strip() or "0")
        except ValueError:
            days = 0
        self.cfg["privacy"] = {
            "proxy_mode": self.proxy_mode_var.get(),
            "proxy_url": proxy_url,
            "auto_clear_history_days": max(0, days),
            "warn_on_address_reuse": bool(self.warn_reuse_var.get()),
        }
        self.cfg["dns"] = {
            "enabled": bool(self.dns_enabled_var.get()),
            "secure_mode": dns_secure_mode,
            "providers": dns_providers,
        }
        cfg.save_config(self.cfg)
        cfg.prune_history(self.cfg["privacy"]["auto_clear_history_days"])

        self._pending_proxy_error = None
        try:
            self.providers = {p.name: p for p in prov.build_providers(self.cfg)}
            # DNS settings (mode, provider list) may have just changed, and
            # build_providers() -> configure_doh_resolvers() already reset
            # the underlying per-hostname dedup state; drop any banner from
            # before the save so it doesn't show stale hostnames under the
            # new settings.
            self._hide_dns_fallback_banner()
        except prov.ProviderError as e:
            # Proxy requested but not usable. Build providers on a direct
            # connection so the app stays usable, and say so loudly, but do
            # NOT write proxy_enabled=False back to disk. Persisting it
            # turned a temporary condition (PySocks not installed yet, Tor
            # not started yet) into a permanent silent downgrade: the next
            # launch built cleanly, showed no banner, and the user's routing
            # preference was simply gone. Keeping the stored preference means
            # the failure re-surfaces at every launch until it's fixed or the
            # user turns it off themselves.
            direct_cfg = copy.deepcopy(self.cfg)
            # proxy_mode, not the pre-0.1.25 proxy_enabled boolean.
            # providers.proxy_mode() reads proxy_mode first and only falls back
            # to proxy_enabled when proxy_mode is absent, so setting the old
            # key here left the copy still saying "on": resolve_proxy returned
            # the same URL, configure_proxy failed again, and build_providers
            # raised a SECOND time from inside this handler. That escaped into
            # Tk's callback hook as an "unexpected error" dialog, so the
            # privacy banner never appeared, self.providers kept the old set,
            # and the one failure path that exists to warn about running on
            # clearnet was the one path that crashed. app.py's equivalent
            # fallback has always used proxy_mode; these two copies had drifted.
            direct_cfg.setdefault("privacy", {})["proxy_mode"] = "off"
            try:
                self.providers = {
                    p.name: p for p in prov.build_providers(direct_cfg)}
            except prov.ProviderError as direct_err:  # pragma: no cover -
                # a direct build has no proxy to fail on, so this is
                # unreachable in practice; it exists so that a future
                # build_providers failure still reaches the banner below
                # rather than the crash handler.
                self._pending_proxy_error = f"{e} (and {direct_err})"
                self._show_proxy_banner(
                    "PRIVACY WARNING: could not build providers on a direct "
                    "connection either. " + str(direct_err))
                self.settings_msg.configure(
                    text=f"Saved, but providers could not be rebuilt: "
                         f"{direct_err}", text_color=BAD)
                return
            self._pending_proxy_error = str(e)
            self._show_proxy_banner(
                "PRIVACY WARNING: running on a DIRECT (clearnet) connection "
                "that exposes your real IP to swap providers. " + str(e))
            self.settings_msg.configure(text=f"Saved, but proxy could not be "
                                              f"enabled: {e}", text_color=BAD)
            self._refresh_credential_banner()
            return

        # Providers built successfully with whatever privacy setting was
        # requested: clear any stale clearnet warning.
        self._hide_proxy_banner()
        self._refresh_route_pill()
        # socks5:// resolves hostnames locally, so the proxy carries the
        # traffic while the user's own resolver still sees every provider
        # hostname. The stricter check above only fires in Secure DNS mode;
        # the leak is the same without it, and the toggle's label promises
        # requests go through the proxy. Warn on save rather than block:
        # some proxies genuinely cannot do remote DNS.
        if (self.proxy_mode_var.get() == "on"
                and self.proxy_url.get().strip().lower().startswith("socks5://")):
            self.settings_msg.configure(
                text=("Saved. Note: socks5:// resolves hostnames on this "
                      "machine, so your DNS resolver still sees every "
                      "provider you contact. Use socks5h:// to have the "
                      "proxy resolve them."),
                text_color=ACCENT)
        else:
            self.settings_msg.configure(text="Saved.", text_color=GOOD)
        self._refresh_credential_banner()
        self._refresh_history()
        # New/changed API keys may unlock a provider's coin-list endpoint
        # (ChangeNOW, FixedFloat) that couldn't be queried before.
        self.on_refresh_coins()

    def on_run_diagnostics(self):
        self.diag_btn.configure(state="disabled", text="Running…")
        self.diag_summary.configure(text="Testing each provider host, both "
                                          "DNS-over-HTTPS resolvers, and a "
                                          "control site…", text_color=MUTED)
        for w in self.diag_results.winfo_children():
            w.destroy()

        def work():
            results = prov.run_diagnostics(list(self.providers.values()))
            self._post(self._show_diagnostics, results)

        threading.Thread(target=work, daemon=True).start()

    def _show_diagnostics(self, results):
        self.diag_btn.configure(state="normal", text="Run Diagnostics")
        self.diag_summary.configure(text=prov.summarize_diagnostics(results),
                                    text_color=MUTED)
        for r in results:
            row = ctk.CTkFrame(self.diag_results, fg_color="transparent")
            row.pack(fill="x", pady=2)
            color = GOOD if r.ok else BAD
            mark = "✓" if r.ok else "✗"
            ctk.CTkLabel(row, text=f"{mark}  {r.label} ({r.host})",
                        text_color=color, font=F(12), anchor="w"
                        ).pack(side="left")
            ctk.CTkLabel(row, text=r.detail, text_color=MUTED, font=F(11),
                        anchor="e").pack(side="right")

    def on_clear_history(self):
        cfg.clear_history()
        self._refresh_history()
        self.settings_msg.configure(text="Local history cleared.", text_color=GOOD)

    # Master password
    def _refresh_mpw_section(self):
        """Sync the master-password status line, button states, and storage
        note to the current encrypted/plaintext state. Called at build time
        and after every set/change/remove."""
        encrypted = cfg.is_config_encrypted()
        if hasattr(self, "_mpw_status"):
            if not cfg.crypto_available():
                self._mpw_status.configure(
                    text="Encryption unavailable (cryptography not installed). "
                         "Keys are stored in plaintext, protected by OS file "
                         "permissions only.", text_color=ACCENT)
            elif encrypted:
                self._mpw_status.configure(
                    text="\U0001f512  Your API keys are ENCRYPTED with a master "
                         "password.", text_color=GOOD)
            else:
                self._mpw_status.configure(
                    text="\u26a0  Your API keys are stored UNENCRYPTED "
                         "(plaintext, OS file permissions only). Set a master "
                         "password to encrypt them at rest.", text_color=ACCENT)
        if cfg.crypto_available() and hasattr(self, "_mpw_set_btn"):
            self._mpw_set_btn.configure(
                state="disabled" if encrypted else "normal")
            self._mpw_change_btn.configure(
                state="normal" if encrypted else "disabled")
            self._mpw_remove_btn.configure(
                state="normal" if encrypted else "disabled")
        if hasattr(self, "_storage_note"):
            path = cfg.CONFIG_ENC_PATH if encrypted else cfg.CONFIG_PATH
            label = "encrypted" if encrypted else "plaintext"
            self._storage_note.configure(
                text=f"Credentials are stored locally ({label}) in {path}. "
                     f"They are affiliate keys for creating swaps, not wallet "
                     f"keys. SwapDesk never has custody of your coins.")

    def on_set_master_password(self):
        self._master_password_dialog("set")

    def on_change_master_password(self):
        self._master_password_dialog("change")

    def on_remove_master_password(self):
        self._master_password_dialog("remove")

    def _master_password_dialog(self, mode: str):
        titles = {"set": "Set a master password",
                  "change": "Change master password",
                  "remove": "Remove encryption"}
        win = ctk.CTkToplevel(self)
        win.title(titles[mode])
        win.configure(fg_color=BG)
        self._center_on_screen(win, 460, 420)
        win.transient(self)
        win.grab_set()
        win.update_idletasks()
        win.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 80}")

        ctk.CTkLabel(win, text=titles[mode], font=F(16, "bold"),
                     text_color=TEXT).pack(pady=(20, 10), padx=20)

        cur = new = conf = None
        if mode in ("change", "remove"):
            ctk.CTkLabel(win, text="Current master password", text_color=MUTED,
                         font=F(12), anchor="w").pack(fill="x", padx=20)
            cur = entry(win, show="\u2022")
            cur.pack(fill="x", padx=20, pady=(4, 10))
            cur.focus_set()
        if mode in ("set", "change"):
            ctk.CTkLabel(win, text="New master password (min 8 characters)",
                         text_color=MUTED, font=F(12), anchor="w"
                         ).pack(fill="x", padx=20)
            new = entry(win, show="\u2022")
            new.pack(fill="x", padx=20, pady=(4, 10))
            if mode == "set":
                new.focus_set()
            ctk.CTkLabel(win, text="Confirm new password", text_color=MUTED,
                         font=F(12), anchor="w").pack(fill="x", padx=20)
            conf = entry(win, show="\u2022")
            conf.pack(fill="x", padx=20, pady=(4, 10))
        if mode == "remove":
            ctk.CTkLabel(win, text="This decrypts your keys back to a plaintext "
                         "config.json, protected only by OS file permissions.",
                         text_color=ACCENT, font=F(11), wraplength=400,
                         justify="left").pack(padx=20, pady=(0, 6))

        err = ctk.CTkLabel(win, text="", text_color=BAD, font=F(12),
                           wraplength=400, justify="left")
        err.pack(padx=20, pady=(4, 0))

        def submit(*_a):
            try:
                if mode == "set":
                    a, b = new.get(), conf.get()
                    if len(a) < 8:
                        err.configure(text="Use at least 8 characters.")
                        return
                    if a != b:
                        err.configure(text="The two passwords don't match.")
                        return
                    cfg.set_master_password(a)
                    msg = "Encryption enabled: your API keys are now encrypted."
                elif mode == "change":
                    a, b = new.get(), conf.get()
                    if len(a) < 8:
                        err.configure(text="Use at least 8 characters.")
                        return
                    if a != b:
                        err.configure(text="The two new passwords don't match.")
                        return
                    if not cfg.change_master_password(cur.get(), a):
                        err.configure(text="Current master password is incorrect.")
                        return
                    msg = "Master password changed."
                else:  # remove
                    if not cfg.remove_master_password(cur.get()):
                        err.configure(text="Current master password is incorrect.")
                        return
                    msg = "Encryption removed: keys are now stored in plaintext."
            except Exception as ex:  # noqa: BLE001 - surface any crypto/IO error
                err.configure(text=str(ex))
                return
            win.destroy()
            self._refresh_mpw_section()
            self.settings_msg.configure(text=msg, text_color=GOOD)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=18)
        secondary_button(btns, "Cancel", win.destroy, height=40).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        action_label = {"set": "Encrypt", "change": "Change",
                        "remove": "Remove encryption"}[mode]
        primary_button(btns, action_label, submit, height=40).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        (conf or cur).bind("<Return>", submit)

    def _refresh_credential_banner(self):
        active = [n for n, p in self.providers.items() if p.configured()]
        if active:
            self.cred_banner.configure(
                text="✓ " + ", ".join(active) + " ready",
                fg_color=GOOD_SOFT, text_color=GOOD)
        else:
            self.cred_banner.configure(
                text="No provider configured. Add keys in Settings",
                fg_color=ACCENT_SOFT, text_color=ACCENT)
