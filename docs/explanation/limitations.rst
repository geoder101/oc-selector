Limitations
===========

What ``oc-selector`` is not, and where its guarantees stop. Reading this page
before relying on the tool saves a surprising amount of debugging.

Not a sandbox
-------------

``oc-selector`` does not isolate the native ``opencode`` process. On POSIX the
wrapper replaces itself with the native executable via ``execve``; on Windows
it runs a supervised subprocess with inherited stdio and working directory.
The native process inherits the user's ``HOME``, data, cache, state, and
authentication exactly as if it had been launched directly. The wrapper adds
configuration-directory selection and OMO profile selection — nothing that
constrains what the native process can do. If you need isolation, it must come
from outside the wrapper.

Not an OpenCode installer
-------------------------

The tool assumes a working ``opencode`` executable already exists. The
registry's ``opencode_executable`` is resolved to one absolute path (a command
name looked up in ``PATH``, or an absolute path), and that is the end of the
wrapper's involvement with the binary. ``oc-selector`` never installs,
downloads, upgrades, or repairs the native executable; a missing or
non-executable target is an error, not something the wrapper fixes.

Registry schema version 1 only
------------------------------

The registry file supports exactly one schema version: ``schema_version = 1``.
Any other value is rejected at load time, and unknown top-level keys or
unknown profile keys are rejected as well. There is no forward-compatibility
shim and no "best effort" parse — a registry the tool does not understand is
an error, not a guess.

No legacy migration
-------------------

Legacy configuration fields are not migrated by any command. The conversion
helpers exist in the package as explicitly invoked, parameterized functions,
but the CLI path never calls them, and ``init`` never converts anything. If a
configuration contains fields the tool does not understand, the tool refuses
rather than rewriting them.

Exit-code limitation on native-forward errors
---------------------------------------------

The exit-code contract distinguishes a missing executable (127) and a
non-executable file (126), and the launcher contains the corresponding code.
**Today, neither code is ever produced.** On POSIX the launcher replaces the
process with ``execve`` (so the 127 return is unreachable), and the
missing-executable, non-executable, and invalid-registry cases are raised in
the top-level dispatcher, which currently exits **1 with a traceback** instead
of a clean classified exit. The verified behavior is documented in
:doc:`/reference/exit-codes`; treat 126/127 as defined-but-unreachable until
the dispatcher is fixed.

Test coverage: what is covered and how
--------------------------------------

The test suite is gated by an exact combined statement-and-branch coverage
threshold of **90%**, computed from raw coverage counts (never the rounded
terminal percentage). The suite currently passes at **93.6%**
(93.5995623632385%) with **zero active exclusions**: the platform-unreachable
lines (the Windows launcher and ``execve`` paths in ``process.py``, and a few
dead guards in ``cli.py``) are *not* excluded — they stay in the denominator
as uncovered, and the gate still passes. The per-file unreachable-line
justifications are recorded as evidence files under
``.omo/evidence/quality-uplift/`` (``t14-unreachable.md``,
``t15-unreachable.md``, ``t16-unreachable.md``).

The suite runs fully isolated: no test touches the real home directory or the
network. Tests use sandboxed home fixtures, fake executables that record their
argv, and proxy environment variables pointed at a dead local port.

Hosted CI verification status
-----------------------------

The quality gate (``just check``: lock-check, format-check, lint, typecheck,
test, build) and the strict docs build (``just docs-check``) are verified
locally on the development machine. **Hosted continuous-integration runs are
unverified until the repository is pushed to GitHub**; the local gate is the
only verification in force today.
