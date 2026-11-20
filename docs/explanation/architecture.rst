Architecture
============

This page explains how ``oc-selector`` is put together: what each module is for,
how an invocation flows from ``sys.argv`` to the native ``opencode`` process, and
why the forwarding is byte-for-byte.

The module map
--------------

The ``oc_selector`` package is 17 small, cohesive, typed modules. Each one
documents its behavioral contract in its module docstring; the one-line roles
below mirror those contracts.

+-----------------+--------------------------------------------------------------+
| Module          | Role                                                         |
+=================+==============================================================+
| ``cli``         | Typer management CLI and raw dispatch integration; the       |
|                 | single console-script entry point (``main``).                |
+-----------------+--------------------------------------------------------------+
| ``routing``     | Pure routing decisions: turns an argv into an immutable      |
|                 | ``NativeRoute`` or ``ManagementRoute``; never invokes Typer  |
|                 | or a child process.                                          |
+-----------------+--------------------------------------------------------------+
| ``registry``    | Typed registry model: parses and validates ``config.toml``   |
|                 | (schema version 1) with tomlkit + Pydantic; owns profile     |
|                 | data and name validation.                                    |
+-----------------+--------------------------------------------------------------+
| ``paths``       | Portable path resolution: config parent, state root,         |
|                 | registry path, and the OMO config path.                      |
+-----------------+--------------------------------------------------------------+
| ``process``     | Native launcher: resolves the executable, builds the launch  |
|                 | environment, and execs (POSIX) or supervises (Windows) the   |
|                 | native process.                                              |
+-----------------+--------------------------------------------------------------+
| ``transactions``| Recoverable file-mutation transactions: journal state        |
|                 | machine, exclusive lock, staging, and pending-journal        |
|                 | discovery.                                                   |
+-----------------+--------------------------------------------------------------+
| ``init_flow``   | Generic init (create parent + empty registry) and the        |
|                 | ``init --recover`` recovery flows.                           |
+-----------------+--------------------------------------------------------------+
| ``wizard``      | Guided create/edit wizards and the registry-only             |
|                 | ``set-default``.                                             |
+-----------------+--------------------------------------------------------------+
| ``agents_link`` | Linking a profile's ``AGENTS.md`` to the global guard.       |
+-----------------+--------------------------------------------------------------+
| ``host_sync``   | Exact host-package sync: detects the package manager in a    |
|                 | profile directory and runs the install inside a transaction. |
+-----------------+--------------------------------------------------------------+
| ``omo_upgrade`` | Schema-validated, exact-version OMO config upgrade inside a  |
|                 | transaction.                                                 |
+-----------------+--------------------------------------------------------------+
| ``omo_profiles``| Parameterized legacy-to-native conversion helpers and modern |
|                 | native entry builders (never called from the CLI path).      |
+-----------------+--------------------------------------------------------------+
| ``jsonc``       | Comment-preserving JSONC adapter built on json-five          |
|                 | ``ModelLoader``/``ModelDumper``.                             |
+-----------------+--------------------------------------------------------------+
| ``diagnostics`` | Read-only diagnostics collector behind ``info``; at most one |
|                 | version-only subprocess, no network.                         |
+-----------------+--------------------------------------------------------------+
| ``completion``  | Non-mutating completion-script generation for bash, zsh,     |
|                 | fish, and PowerShell.                                        |
+-----------------+--------------------------------------------------------------+
| ``errors``      | Typed error hierarchy; every error carries a                 |
|                 | machine-readable code and a public exit code.                |
+-----------------+--------------------------------------------------------------+
| ``__init__``    | Package marker and the single ``__version__`` constant.      |
+-----------------+--------------------------------------------------------------+

The routing and dispatch flow
-----------------------------

Every invocation starts in ``cli.main``. It reads ``sys.argv[1:]``, loads the
registry if the registry file exists (an absent registry is *not* an error at
this point), and hands the argv to ``routing.route``. Routing inspects **only
the first token** and produces one of two immutable route values:

.. code-block:: text

    sys.argv[1:]
         |
         v
    +----------------+   empty argv            +--------------------------------+
    |  routing.route | ----------------------> | NativeRoute(default profile)   |
    +----------------+                         +--------------------------------+
         |  first token is "--"
         +-----------------------------------> NativeRoute(rest, default profile)
         |  first token is "--help"/"--version"
         +-----------------------------------> NativeRoute(whole argv, native)
         |  first token is a management name
         +-----------------------------------> ManagementRoute(command, args)
         |  first token is a registered profile
         +-----------------------------------> NativeRoute(tail, that profile)
         |  anything else
         +-----------------------------------> NativeRoute(whole argv, default)

Back in ``main``:

- A ``ManagementRoute`` is dispatched to the Typer app with the command and its
  remaining args. The management grammar (``list``, ``info``, ``init``,
  ``create``, ``edit``, ``set-default``, ``sync``, ``upgrade-omo``,
  ``completion``, ``help``) is validated exactly; violations are argument
  errors.
- A ``NativeRoute`` requires a registry (no registry is a clean exit 2 with
  init guidance), resolves the registered ``opencode_executable`` to one
  absolute path, and calls ``process.launch_native``.

The routing rules are deliberately strict:

- A leading ``--`` removes exactly one token and forces default/native
  forwarding of the rest — it is the escape hatch for native arguments that
  would otherwise look like a profile name.
- Root ``--help`` and ``--version`` are native, not wrapper options: the native
  ``opencode`` handles them.
- Unknown first tokens are forwarded unchanged to the native command under the
  default profile; the wrapper never invents flags of its own outside the
  management parser.

Byte-for-byte argv forwarding
-----------------------------

A ``NativeRoute`` carries its tail as an immutable tuple of strings. The
launcher builds ``[executable, *tail]`` and passes it to the OS unchanged:

- On POSIX, the wrapper calls ``execve`` with that argv and the launch
  environment. The wrapper process is *replaced* by the native process, so
  there is no second PATH lookup, no argument re-quoting, and no intermediate
  layer that could alter an argument. Whatever the user typed after the
  profile name reaches ``opencode`` byte-for-byte.
- On Windows, the wrapper runs a supervised subprocess with inherited stdio
  and working directory, again passing the argv list through unchanged, and
  exits with the native process's own exit code.

The only things the wrapper adds are environment variables, never arguments:

- ``OPENCODE_CONFIG_DIR`` is always set to the exact selected profile
  directory — this is how profile selection takes effect.
- For a profile that declares an ``omo_profile`` (an *owned* profile), the
  wrapper sets ``OMO_PROFILE`` to the registered value and clears
  ``OCX_PROFILE``. For a profile without one (*unmanaged*), both inherited
  values are preserved unchanged.

State layout
------------

All state lives under one config parent: the absolute ``XDG_CONFIG_HOME`` if
set (relative or empty values are rejected), otherwise ``~/.config``.

.. code-block:: text

    <config parent>/oc-selector/          # state root
        config.toml                       # the registry (schema version 1)
        maintenance.lock                  # the single exclusive lock
        journals/<id>.json                # transaction journals
        staging/<id>/                     # staged files for a transaction
        backups/<id>/                     # rollback material

The registry file is the only file the user is expected to read or edit; the
rest is transaction machinery. The OMO config lives elsewhere — at the actual
home ``~/.omo`` — and is explained in :doc:`omo-separation`.
