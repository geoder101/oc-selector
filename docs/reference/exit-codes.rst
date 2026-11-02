Exit codes
==========

The exit codes ``oc-selector`` actually produces. These were verified against the
running command, not against the error-type definitions in the source.

+---------------------+-------------------------------------------------------------+
| Code                | Meaning                                                     |
+=====================+=============================================================+
| ``0``               | Success                                                     |
+---------------------+-------------------------------------------------------------+
| ``1``               | Maintenance or argument error                               |
+---------------------+-------------------------------------------------------------+
| ``2``               | Argument or registry error (clean, from a management cmd)   |
+---------------------+-------------------------------------------------------------+
| ``130``             | User interruption (Ctrl+C)                                  |
+---------------------+-------------------------------------------------------------+
| *native-forwarded*  | The native ``opencode`` command's own exit code             |
+---------------------+-------------------------------------------------------------+

0 — success
-----------

A management command that completes normally exits 0 (``init``, ``create``,
``edit``, ``set-default``, ``list``, ``info``, ``sync``, ``upgrade-omo``,
``completion``, ``help``). ``info`` exits 0 even when the setup is unhealthy,
because diagnostics are data, not errors.

When a native command runs, the wrapper replaces itself with the native
``opencode`` process, so the exit code is the native command's own exit code. A
native command that succeeds exits 0.

1 — maintenance or argument error
---------------------------------

Exit 1 covers two distinct situations:

- **Wizard-level argument errors.** When a management command's underlying
  operation fails validation, the command exits 1 with a clean ``Error:`` line.
  This is the case for:

  - ``create`` with an invalid, reserved, or duplicate-directory name;
  - ``edit`` or ``set-default`` naming a profile that does not exist;
  - ``sync`` when a package sync fails.

  .. code-block:: console

      $ oc-selector set-default nonexistent
      Error: Profile 'nonexistent' not found

- **Native-forward and registry-load errors (with a traceback).** The
  native-forward error paths — no registry, a missing executable, and a
  non-executable executable — currently exit **1 with a traceback**. This is
  because the top-level dispatcher raises ``typer.Exit`` outside of the command
  parser's context, so the exception is not converted into a clean exit. The
  same applies to an **invalid registry file**, which is loaded in that same
  top-level dispatcher:

  .. code-block:: console

      $ oc-selector --model openai/gpt-4 "fix the bug"
      Error: No registry found. Run 'oc-selector init' first.
      Traceback (most recent call last):
        ...
      typer.exceptions.Exit: 2

  The ``Error:`` line is printed; the traceback is a known limitation, not a
  signal that the exit code is 2.

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

Note the contrast with the exit-1 case above: the *missing* registry is a clean
exit 2 when checked by a management command, but an *invalid* registry file is
an exit 1 with a traceback because it is loaded earlier, in the top-level
dispatcher.

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

The source defines error types for a missing executable (``127``) and a
non-executable file (``126``), and the launcher contains a ``return 127``.
**Neither code is ever produced by the command today.** On POSIX the launcher
replaces the process with ``execve`` (so the ``return 127`` is unreachable), and
the missing-executable and non-executable cases are raised in the top-level
dispatcher, which exits **1 with a traceback** as described above. They are
therefore documented here as exit 1, not as distinct 126/127 codes.
