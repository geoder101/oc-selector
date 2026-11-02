Command-line reference
======================

``oc-selector`` is a single command with two kinds of invocation:

- **Management commands** — a fixed set of subcommands (``init``, ``create``,
  ``list``, ...) that the wrapper handles itself in order to maintain the
  registry.
- **Native forwarding** — anything else is forwarded, byte-for-byte, to the
  native ``opencode`` executable selected by the registry.

Run ``oc-selector help`` to print the management grammar, or
``oc-selector <command> --help`` for a single command's options.

.. note::

    A root ``--help`` or ``--version`` is **not** a wrapper option: it is
    forwarded to the native ``opencode``. Use ``oc-selector help`` (and
    ``oc-selector help --version``) for the wrapper's own usage and version.

Management grammar
------------------

The exact grammar, as printed by ``oc-selector help``:

.. code-block:: text

    list [--json]
    info [--json]
    init [--no-backup] [--dry-run] [--recover]
    create NAME [DIRECTORY] [--model MODEL] [--omo-profile OMO_PROFILE] [--no-interactive] [--dry-run]
    edit PROFILE [--directory DIR] [--model MODEL] [--omo-profile OMO_PROFILE] [--dry-run]
    set-default PROFILE [--dry-run]
    sync [PROFILE] [--all] [--dry-run]
    upgrade-omo [PROFILE] [--version VERSION] [--all] [--dry-run]
    completion SHELL [--alias NAME]
    help [--version]

Command summary
---------------

+----------------+------------------------------------------------------+
| Command        | Purpose                                              |
+================+======================================================+
| ``init``       | Create the config directory and an empty registry    |
+----------------+------------------------------------------------------+
| ``create``     | Add a new profile                                    |
+----------------+------------------------------------------------------+
| ``edit``       | Change fields of an existing profile                 |
+----------------+------------------------------------------------------+
| ``set-default``| Set the default profile                              |
+----------------+------------------------------------------------------+
| ``list``       | List all registered profiles                         |
+----------------+------------------------------------------------------+
| ``info``       | Collect and display diagnostics                      |
+----------------+------------------------------------------------------+
| ``sync``       | Sync the host package for selected profiles          |
+----------------+------------------------------------------------------+
| ``upgrade-omo``| Upgrade the OMO config to a target version           |
+----------------+------------------------------------------------------+
| ``completion`` | Generate a shell completion script                   |
+----------------+------------------------------------------------------+
| ``help``       | Show the wrapper usage and the management grammar    |
+----------------+------------------------------------------------------+

The exit codes for every command are documented in :doc:`exit-codes`.

init
----

.. code-block:: text

    oc-selector init [--no-backup] [--dry-run] [--recover]

Initialize the configuration. Creates the config parent directory and an empty
registry containing a single ``default`` profile that points at a ``default``
directory. The command is idempotent: running it again reports
``Already initialized.`` and changes nothing.

Options:

- ``--no-backup`` — skip backups (inert for ``init``).
- ``--dry-run`` — preview without writing.
- ``--recover`` — recover pending transactions instead of initializing.

``--recover`` cannot be combined with ``--no-backup`` or ``--dry-run`` (exit 2).

.. code-block:: console

    $ oc-selector init
    Created config directory: /home/u/.config
    Created registry: /home/u/.config/oc-selector/config.toml

Exit codes: 0 on success; 2 on a registry or argument error.

create
------

.. code-block:: text

    oc-selector create NAME [DIRECTORY] [--model MODEL] [--omo-profile OMO_PROFILE] [--no-interactive] [--dry-run]

Add a new profile. ``NAME`` is required; ``DIRECTORY`` is optional and, when
omitted, is prompted for unless ``--no-interactive`` is set or stdin is not a
terminal.

Arguments:

- ``NAME`` (required) — the profile name.
- ``DIRECTORY`` (optional) — the profile's settings directory.

Options:

- ``--model``, ``-m`` — pin a model ID for the profile.
- ``--omo-profile`` — associate an OMO profile name with the profile.
- ``--no-interactive`` — do not prompt; ``DIRECTORY`` must be given.
- ``--dry-run`` — preview without writing.

.. code-block:: console

    $ oc-selector create work work-dir --model openai/gpt-4
    Created profile 'work'

Exit codes: 0 on success; 2 when the registry is absent or the directory is
required but missing; 1 when the name is invalid, reserved, or the directory is
already used by another profile.

edit
----

.. code-block:: text

    oc-selector edit PROFILE [--directory DIR] [--model MODEL] [--omo-profile OMO_PROFILE] [--dry-run]

Change fields of an existing profile. Only the fields that are named change; the
rest are preserved.

Arguments:

- ``PROFILE`` (required) — the profile to edit.

Options:

- ``--directory``, ``-d`` — new settings directory.
- ``--model``, ``-m`` — new model ID.
- ``--omo-profile`` — new OMO profile name.
- ``--dry-run`` — preview without writing.

.. code-block:: console

    $ oc-selector edit work --model anthropic/claude-3
    Edited profile 'work'

Exit codes: 0 on success; 2 when the registry is absent; 1 when the profile does
not exist or a field is invalid.

set-default
-----------

.. code-block:: text

    oc-selector set-default PROFILE [--dry-run]

Set the default profile. This is a registry-only change: it does not touch any
settings directory.

Arguments:

- ``PROFILE`` (required) — the profile to make the default.

Options:

- ``--dry-run`` — preview without writing.

.. code-block:: console

    $ oc-selector set-default work
    Set default to 'work'

Exit codes: 0 on success; 2 when the registry is absent; 1 when the profile does
not exist.

list
----

.. code-block:: text

    oc-selector list [--json]

List all registered profiles. In the human view the default profile is marked
with ``*`` and a pinned model is shown in parentheses.

Options:

- ``--json`` — emit machine-readable JSON.

.. code-block:: console

    $ oc-selector list
      default -> default
    * work -> work-dir (openai/gpt-4)
      personal -> personal-dir

Exit codes: 0 on success; 2 when the registry is absent.

info
----

.. code-block:: text

    oc-selector info [--json]

Collect and display diagnostics for the current setup: the wrapper version, the
resolved paths, the native ``opencode`` version (probed with a single bounded
``--version`` call), the per-profile status, and the lock and recovery state.
Diagnostics are data, not errors: an unhealthy setup still exits 0.

Options:

- ``--json`` — emit the full machine-readable object.

.. code-block:: console

    $ oc-selector info
    OpenCode: 1.19.5
    Default profile: default
    Profiles: 2
      default: warning
      work: warning

Exit codes: 0 (diagnostics are data, not errors).

sync
----

.. code-block:: text

    oc-selector sync [PROFILE] [--all] [--dry-run]

Sync the host package into the selected profile's directory. With no argument
and no ``--all``, the default profile is used.

Arguments:

- ``PROFILE`` (optional) — the profile to sync.

Options:

- ``--all`` — sync every profile.
- ``--dry-run`` — preview without running the package manager.

``PROFILE`` and ``--all`` are mutually exclusive (exit 2).

Exit codes: 0 on success; 2 when the registry is absent, the profile is unknown,
or both ``PROFILE`` and ``--all`` are given; 1 when a package sync fails.

upgrade-omo
-----------

.. code-block:: text

    oc-selector upgrade-omo [PROFILE] [--version VERSION] [--all] [--dry-run]

Upgrade the OMO config to a target version. Only profiles that declare an
``omo_profile`` are eligible. With no argument and no ``--all``, the default
profile is used.

Arguments:

- ``PROFILE`` (optional) — the profile to upgrade.

Options:

- ``--version`` — the target OMO version (otherwise resolved from the host
  executable).
- ``--all`` — upgrade every eligible profile.
- ``--dry-run`` — preview without writing.

``PROFILE`` and ``--all`` are mutually exclusive (exit 2).

Exit codes: 0 on success; 2 when the registry is absent, the profile is unknown,
the OMO config is missing or invalid, or both ``PROFILE`` and ``--all`` are
given; 1 when the upgrade itself fails.

completion
----------

.. code-block:: text

    oc-selector completion SHELL [--alias NAME]

Generate a shell completion script. Works with or without a registry.

Arguments:

- ``SHELL`` (required) — one of ``bash``, ``zsh``, ``fish``, ``powershell``.

Options:

- ``--alias`` — register the completions under an alias name.

.. code-block:: console

    $ eval "$(oc-selector completion bash)"

Exit codes: 0 on success.

help
----

.. code-block:: text

    oc-selector help [--version]

Show the wrapper usage and the management grammar.

Options:

- ``--version`` — print the wrapper version instead of the grammar.

.. code-block:: console

    $ oc-selector help --version
    0.1.0

Exit codes: 0 on success.

Native forwarding
-----------------

Any invocation that is not a management command is forwarded to the native
``opencode`` executable. The routing rules, in order:

- No arguments — the default profile is selected and an empty tail is forwarded.
- A leading ``--`` — removes exactly one token and forces default/native
  forwarding of the rest.
- A registered profile name as the first token — selects that profile and
  forwards the remaining tail byte-for-byte.
- ``--help`` or ``--version`` as the first token — forwarded to native.
- Anything else — forwarded unchanged to the default profile.

The selected profile's settings directory is passed to the native command via
the ``OPENCODE_CONFIG_DIR`` environment variable; all remaining arguments are
preserved byte-for-byte. The wrapper exits with the native command's own exit
code. See :doc:`exit-codes` for the details.
