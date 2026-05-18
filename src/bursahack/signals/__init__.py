"""Re-export every Strategy subclass so `Strategy.__subclasses__()` sees them.

The registry walks the subclass tree; classes only appear there if their
defining module has been imported. This file is the single import point.
"""
from bursahack.signals.breakout import *  # noqa: F401, F403
from bursahack.signals.clenow_som import *  # noqa: F401, F403
from bursahack.signals.momentum import *  # noqa: F401, F403
from bursahack.signals.reversal import *  # noqa: F401, F403
from bursahack.signals.rotation import *  # noqa: F401, F403
from bursahack.signals.timeseries_momentum import *  # noqa: F401, F403
