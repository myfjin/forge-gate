# Changelog

## 0.2.0 (unreleased): Forge Gate is now ForgeProof

- **Renamed** (2026-11-25): the project, the Python package and the command are now `forgeproof`.
  The name `forgegate` was already taken on PyPI and npm by another tool for AI agents, so we
  changed ours before the first release.
- **For one release** the old names still work and say they were renamed:
  - `import forge_gate` and `import forge_gate.<module>` give the same modules as `forgeproof`,
    with a `DeprecationWarning`;
  - the `forge-gate` command prints a notice and runs `forgeproof`.
- **Unchanged on purpose:** the `FORGE_GATE_*` environment variables (a later release reads
  `FORGEPROOF_*` first and keeps accepting the old ones), the `forge-gate-ratify` signature
  namespace, the `# forge-gate-result:` marker in handed-out code, and the fixture protocol line.
  Existing signatures, ledgers and handed-out code stay valid.

## 0.1.0

- First public alpha as Forge Gate.
