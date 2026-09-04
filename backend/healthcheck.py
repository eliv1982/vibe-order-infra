"""Stage 3: Docker healthcheck for the `backend` container.

Invoked by Dockerfile's HEALTHCHECK instruction only (see backend/Dockerfile)
- not part of the application's own HTTP surface. Uses only the standard
library (urllib) rather than curl/wget, which the python:*-slim base image
does not include and which Stage 3 avoids adding purely for this. Calls
GET /api/ready on the backend's own loopback address (same container network
namespace, not through Nginx - see app/main.py::readiness_check for what
that endpoint actually checks and why it, not /api/health, is the right
target for a container-readiness healthcheck). Exit code 0 means Docker
should consider the container healthy; any other exit code means unhealthy.
Never prints response bodies/exception details - Docker's healthcheck log
already captures stdout/stderr, and the failure reasons this could leak
(connection errors, timeouts) carry no sensitive data anyway, but keeping
this script silent on success/expected-failure paths keeps `docker inspect`
health logs uncluttered.
"""

import sys
import urllib.request

READY_URL = "http://127.0.0.1:8000/api/ready"


def main() -> int:
    try:
        with urllib.request.urlopen(READY_URL, timeout=2) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
