"""The ``forge-gate`` command: renamed ``forgeproof`` on 2026-11-25. Kept for one release."""

import sys

NOTICE = ("forge-gate: this command was renamed to 'forgeproof' (2026-11-25). "
          "Running 'forgeproof' now; please update your scripts.")


def main(argv=None) -> int:
    print(NOTICE, file=sys.stderr)
    from forgeproof.cli import main as forgeproof_main
    return forgeproof_main(argv)


if __name__ == "__main__":
    sys.exit(main())
