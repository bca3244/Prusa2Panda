"""Start a built GUI binary and check that it is still running after a few seconds.

    python tools/smoke_gui.py dist/Prusa2Panda.app/Contents/MacOS/Prusa2Panda

A GUI that is missing a bundled module, or Tcl/Tk, exits straight away with a traceback;
one that gets as far as its main loop keeps running until it is killed.
"""

import subprocess
import sys
import time

WAIT = 10  # seconds

if __name__ == "__main__":
    proc = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.5)
    if proc.poll() is not None:
        out = proc.stdout.read().decode(errors="replace")
        print(out)
        print(f"GUI exited early with code {proc.returncode}", file=sys.stderr)
        sys.exit(1)
    proc.kill()
    proc.wait()
    print(f"GUI still running after {WAIT}s: OK")
