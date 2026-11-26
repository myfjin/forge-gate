"""The ``forge-gate`` command: renamed ``forgeproof`` on 2026-11-25. Kept until 0.3.0, which removes it."""

import sys

NOTICE = ("forge-gate: this command was renamed to 'forgeproof' (2026-11-25). "
          "Running 'forgeproof' now; please update your scripts: 'forge-gate' is removed in 0.3.0.")


def main(argv=None) -> int:
    print(NOTICE, file=sys.stderr)
    from forgeproof.cli import main as forgeproof_main
    return forgeproof_main(argv)


if __name__ == "__main__":
    sys.exit(main())
