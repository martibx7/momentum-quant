"""
Wrapper script to run the momentum-quant scanner with error filtering.
This script launches run_live.py but filters out any unwanted IB error messages.
"""

import subprocess
import sys
import re

# Regex pattern to match unwanted IB error messages:
#   - 162: API scanner subscription cancelled
#   - 321: generic-tick or bad-duration format errors
error_pattern = re.compile(
    r".*ERROR: Error (?:162|321).*"
)

# Python executable and command
python_exe = sys.executable
cmd = [python_exe, "-m", "scripts.run_live"]

proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1  # line-buffered
)

try:
    for line in proc.stdout:
        if not error_pattern.match(line):
            print(line, end="")
except KeyboardInterrupt:
    print("\nKeyboard interrupt detected, shutting down.")
    proc.terminate()
    sys.exit(0)
