Exit codes
==========

The exit codes ``oc-selector`` actually produces. These were verified against the
running command, not against the error-type definitions in the source.

+---------------------+-------------------------------------------------------------+
| Code                | Meaning                                                     |
+=====================+=============================================================+
| ``0``               | Success                                                     |
+---------------------+-------------------------------------------------------------+
| ``1``               | Operation or maintenance error (clean ``Error:`` line)      |
+---------------------+-------------------------------------------------------------+
| ``2``               | Argument/usage or registry error (clean)                    |
+---------------------+-------------------------------------------------------------+
| ``126``             | Executable present but not executable                       |
+---------------------+-------------------------------------------------------------+
| ``127``             | Executable not found                                        |
+---------------------+-------------------------------------------------------------+
| ``130``             | User interruption (Ctrl+C)                                  |
+---------------------+-------------------------------------------------------------+
| *native-forwarded*  | The native ``opencode`` command's own exit code             |
+---------------------+-------------------------------------------------------------+

0 — success
-----------

A management command that completes normally exits 0 (``init``, ``create``,
``edit``, ``delete``, ``set-default``, ``link``, ``list``, ``info``, ``sync``,
``upgrade-omo``, ``completion``, ``help``). ``info`` exits 0 even when the setup is unhealthy,
because diagnostics are data, not errors.

When a native command runs, the wrapper replaces itself with the native
``opencode`` process, so the exit code is the native command's own exit code. A
native command that succeeds exits 0.

1 — operation or maintenance error
----------------------------------

Exit 1 is produced when a management command's operation fails:

- **Operation-validation failures.** When a command's underlying operation
  fails validation, the command exits 1 with a clean ``Error:`` line. This is
  the case for:

  - ``create`` with an invalid, reserved, or duplicate name;
  - ``edit`` or ``set-default`` naming a profile that does not exist.

  .. code-block:: console

      $ oc-selector set-default nonexistent
      Error: Profile 'nonexistent' not found

- **Maintenance-operation failures.** ``sync`` when a package sync fails, or
  ``init --recover`` when a pending journal cannot be restored. Each failing
  target or journal is reported to stderr; the command exits 1 so a partial
  run is never mistaken for success.

  .. code-block:: console

      $ oc-selector init --recover
      Failed: rec-fail - Cannot restore /home/user/project/target

2 — argument or registry error
------------------------------

Exit 2 is produced by the clean, in-command checks of the management commands:

- a management command run with **no registry** (``list``, ``create``, ``edit``,
  ``set-default``, ``sync``, ``upgrade-omo``);
- ``sync`` or ``upgrade-omo`` naming an **unknown profile**;
- ``sync`` or ``upgrade-omo`` given **both** a ``PROFILE`` and ``--all``;
- ``init`` given ``--recover`` together with ``--no-backup`` or ``--dry-run``;
- ``upgrade-omo`` with a missing or invalid OMO config;
- ``create`` with a required-but-missing or empty directory.

.. code-block:: console

    $ oc-selector list
    Error: No registry found. Run 'oc-selector init' first.

The registry is loaded by the top-level dispatcher before routing. A *missing*
registry and an *invalid* registry file are both reported as a clean exit 2, so
there is no traceback either way.

130 — user interruption
-----------------------

Ctrl+C (SIGINT) exits 130, the conventional interruption status.

Native-forwarded exit code
--------------------------

When a native command runs, the wrapper hands the process over to the native
``opencode`` executable and exits with whatever code that command returns. For
example, a native command that exits 42 makes ``oc-selector`` exit 42.

About 126 and 127
-----------------

The launcher distinguishes the two launcher-level failures and honors each
error type's own code, exiting cleanly (no traceback):

- **127 — missing executable.** Resolving ``opencode_executable`` finds no
  regular file. ``main`` prints ``Error: Executable not found in PATH: <name>``
  for a command name absent from ``PATH`` (or
  ``Error: Executable not found: <path>`` for an absolute path) and exits 127.
- **126 — non-executable executable.** The resolved file is not executable.
  ``main`` prints ``Error: Not executable: <path>`` and exits 126.

Both errors are raised before the native process is launched, so they surface
from the Python-level dispatcher rather than from a failed ``execve``.
