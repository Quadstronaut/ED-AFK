"""
Dummy long-lived child for the OS-process acceptance repros.

Used by AC1/AC2 (orphan / fallback) as a stand-in for `cli.py run` spawned
through the SAME Start-ChildInJob path the launcher uses. Modes:

  long [seconds]   sleep N seconds (default 3600) — a long-lived child to kill.
  exit  <code>     print a marker then exit with <code> — for AC6 (exit-code).
  echo  <line>...  print each line to stdout AND stderr — for AC7 (passthrough).
  cwd              print the effective CWD — for AC8.

Stdout is line-buffered + flushed so the parent sees lines live.
"""

import os
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "long"

    if mode == "long":
        secs = float(args[1]) if len(args) > 1 else 3600.0
        print(f"DUMMY_CHILD_STARTED pid={os.getpid()}", flush=True)
        time.sleep(secs)
        print("DUMMY_CHILD_TIMED_OUT", flush=True)
        return 0

    if mode == "exit":
        code = int(args[1]) if len(args) > 1 else 0
        print(f"DUMMY_CHILD_EXIT code={code}", flush=True)
        return code

    if mode == "echo":
        for line in args[1:]:
            print(f"OUT::{line}", flush=True)
            print(f"ERR::{line}", file=sys.stderr, flush=True)
        return 0

    if mode == "cwd":
        print(f"CWD::{os.getcwd()}", flush=True)
        return 0

    print(f"unknown mode {mode!r}", file=sys.stderr, flush=True)
    return 64


if __name__ == "__main__":
    sys.exit(main())
