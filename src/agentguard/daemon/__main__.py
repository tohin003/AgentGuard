"""``python -m agentguard.daemon run`` — how the shim spawns a detached daemon."""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "run":
        from agentguard.daemon.app import run

        host = "127.0.0.1"
        port = 0
        for i, a in enumerate(args):
            if a == "--host" and i + 1 < len(args):
                host = args[i + 1]
            if a == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
        run(host=host, port=port)
        return 0
    sys.stderr.write("usage: python -m agentguard.daemon run [--host H] [--port P]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
