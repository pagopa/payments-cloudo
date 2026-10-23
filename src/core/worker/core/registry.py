"""In-memory registry of the executions running on this worker instance."""

import subprocess
from threading import Lock

# exec_id -> run metadata, used for duplicate detection and /processes
ACTIVE_RUNS = {}
ACTIVE_LOCK = Lock()
# exec_id -> running subprocess, used by /processes/stop
PROCESS_BY_EXEC: dict[str, subprocess.Popen] = {}
