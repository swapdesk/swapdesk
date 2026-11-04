"""SwapDesk UI tab mixins, split out of the app.py monolith."""
from .swap_tab import SwapTabMixin
from .deposit import DepositMixin
from .settings_tab import SettingsTabMixin
from .history_tab import HistoryTabMixin

__all__ = [  # noqa: RUF022  (tab order, not alphabetical)
    "SwapTabMixin", "DepositMixin", "SettingsTabMixin", "HistoryTabMixin",
]
