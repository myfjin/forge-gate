# Changelog

## 0.2.1 (2026-11-26): settings are `FORGEPROOF_*`

- **Settings renamed** (rename slice B): every setting is now `FORGEPROOF_<NAME>`, for example
  `FORGEPROOF_HOME`, `FORGEPROOF_LIBRARY_DIR`, `FORGEPROOF_CONFIG`, `FORGEPROOF_SCRATCH`,
  `FORGEPROOF_NODE`. This holds in the environment and in `config.toml` alike.
- **The old names still work:** when `FORGEPROOF_<NAME>` is unset, `FORGE_GATE_<NAME>` is read
  instead, so an existing environment or `config.toml` needs no change. When both are set and
  differ, `FORGEPROOF_<NAME>` wins, and one notice on stderr says so.
- **`forgeproof --version`** prints `forgeproof <version>` and exits 0 (before, it printed the usage
  and exited 2). `forge-gate --version` prints the same, after its notice.
- **Removal date for the old names: 0.3.0.** The `forge-gate` command, `import forge_gate`, and the
  `FORGE_GATE_*` fallback for settings all go in 0.3.0. Their notices now say so: a setting read
  under its old name prints one line on stderr (once per process, never on stdout). Before
  upgrading to 0.3.0, rename `FORGE_GATE_<NAME>` to `FORGEPROOF_<NAME>` in your environment and
  `config.toml`.
- **Unchanged on purpose:** the `FORGE_GATE_RESULT` fixture protocol line, the `forge-gate-ratify`
  signature namespace and the `# forge-gate-result:` marker. They are data formats, not settings.

## 0.2.0 (2026-11-26): Forge Gate is now ForgeProof

- **Renamed** (2026-11-25): the project, the Python package and the command are now `forgeproof`.
  The name `forgegate` was already taken on PyPI and npm by another tool for AI agents, so we
  changed ours before the first release.
- **For one release** the old names still work and say they were renamed:
  - `import forge_gate` and `import forge_gate.<module>` give the same modules as `forgeproof`,
    with a `DeprecationWarning`;
  - the `forge-gate` command prints a notice and runs `forgeproof`.
- **An install runs every lens** (KD-2): the lens tools (metamorphic, invariant, twin-consensus,
  guard) moved from `tools/` into the package, `forgeproof/lens_tools/`. `pip install` alone now
  gives a correct file `PASS`; before, without `FORGE_GATE_HOME` pointing at a checkout, five
  lenses went ERROR and the verdict was `UNSTABLE`. `FORGE_GATE_HOME` is no longer read for any
  lens. On PyPI the distribution is `forgeproof-cli`.
- **Unchanged on purpose:** the `FORGE_GATE_*` environment variables (a later release reads
  `FORGEPROOF_*` first and keeps accepting the old ones), the `forge-gate-ratify` signature
  namespace, the `# forge-gate-result:` marker in handed-out code, and the fixture protocol line.
  Existing signatures, ledgers and handed-out code stay valid.

## 0.1.0

- First public alpha as Forge Gate.
