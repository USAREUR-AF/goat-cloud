"""Data layer re-export — maintains backwards compatibility.

The actual implementation lives in focused sub-modules (data_core, data_reports,
data_compliance, data_terraform, data_security, data_cost, data_resources).
This file re-exports everything so existing `from data import X` statements
continue to work.
"""

from data_compliance import *  # noqa: F403
from data_core import *  # noqa: F403
from data_core import _decimal_to_float  # noqa: F401 — used by tests
from data_cost import *  # noqa: F403
from data_reports import *  # noqa: F403
from data_reports import _load_report_from_dynamodb, _reassemble_report  # noqa: F401 — used by tests
from data_resources import *  # noqa: F403
from data_security import *  # noqa: F403
from data_terraform import *  # noqa: F403
from data_terraform import _MODULE_FILE_MAP, _load_tf_state  # noqa: F401 — used by views/arch
