# Security

Forge Gate runs code it is asked to check. If you find a way for that code to reach
anything outside its sandbox (files, network, other users), or any other security
problem, please report it privately first:
[GitHub private vulnerability reporting](https://github.com/myfjin/forge-gate/security/advisories/new).
It reaches only the maintainers.

Please include what you ran, on which platform, and what you observed. You will get an
answer, and credit in the fix if you want it.

The isolation box can be checked on any Linux host with `forge-gate box-selftest`: it
tries to read a file, write outside the box and reach the network, boxed and unboxed,
and passes only when every boxed attempt is blocked and every unboxed one gets through.
