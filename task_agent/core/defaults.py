"""Single source of truth for service URLs and HTTP timeouts.

All URLs default to empty and should be configured via environment variables
or config.yaml for your deployment.
"""

import os

REDMINE_URL = os.environ.get("REDMINE_URL", "")
REDMINE_ISSUES_URL = f"{REDMINE_URL}/issues" if REDMINE_URL else ""

HTTP_TIMEOUT = 30
POST_TIMEOUT = 15
