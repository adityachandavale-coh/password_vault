"""Start HomeHub from any working directory.

`python -m homehub` needs the project root on sys.path, which only happens when
the process starts there. Service managers (systemd, launchd, Task Scheduler)
often run with a different working directory, so point them at this file:

    /path/to/.venv/bin/python /path/to/scripts/run.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homehub.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
