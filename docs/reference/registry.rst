Registry file reference
=======================

The registry is a single TOML file that records the named settings profiles, the
default profile, and the native executable to forward to.

Location
--------

The registry lives at::

    $XDG_CONFIG_HOME/oc-selector/config.toml

When ``XDG_CONFIG_HOME`` is unset, the parent defaults to ``~/.config``, so the
registry is ``~/.config/oc-selector/config.toml``. A relative or empty
``XDG_CONFIG_HOME`` is rejected. The state root (backups, journals, and the
maintenance lock) is the registry file's parent directory, ``$XDG_CONFIG_HOME/oc-selector``.

Complete example
----------------

The following file is a complete, valid registry. It round-trips: writing it to
``$XDG_CONFIG_HOME/oc-selector/config.toml`` and running ``oc-selector list``
succeeds.

.. code-block:: toml

    # Registry schema version. Only 1 is supported.
    schema_version = 1

    # The profile selected when no profile name is given on the command line.
    # Must name a profile defined below.
    default_profile = "work"

    # The native OpenCode executable to forward to: a command name looked up on
    # PATH, or an absolute path. Default: "opencode".
    opencode_executable = "opencode"

    # One table per profile; the table key is the profile name.
    [profiles.work]
    # Required. The settings directory for this profile, relative to the config
    # parent ($XDG_CONFIG_HOME).
    directory = "work-dir"
    # Optional. Pin a model ID for this profile.
    model = "openai/gpt-4"

    [profiles.personal]
    # Required.
    directory = "personal-dir"
    # Optional. Associate an OMO profile (used by `upgrade-omo`).
    omo_profile = "personal-omo"

Keys
----

Top-level keys:

``schema_version``
    Integer. Default ``1``; only ``1`` is supported.

``default_profile``
    String. **Required.** Must name an existing profile.

``opencode_executable``
    String. Default ``"opencode"``. A command name looked up on ``PATH``, or an
    absolute path; a bare shell string is rejected.

``profiles``
    Table. **Required** to hold at least one profile.

Per-profile keys (under ``[profiles.<name>]``):

``directory``
    String. **Required**, non-empty. Relative to the config parent.

``model``
    String. Optional. A pinned model ID.

``omo_profile``
    String. Optional. The OMO profile associated with this profile.

Unknown keys at either level are rejected.

Validation rules
----------------

- **Name format.** Profile names must match ``[a-z][a-z0-9-]{0,47}`` — lowercase,
  starting with a letter, at most 48 characters.
- **Reserved names.** Management command names (``list``, ``init``, ``create``,
  ``edit``, ``delete``, ``set-default``, ``sync``, ``upgrade-omo``, ``link``,
  ``completion``, ``help``, ``info``), native OpenCode subcommand names (``run``,
  ``agent``, ``auth``, ``mcp``, ...), and Windows device names (``con``, ``nul``,
  ``com1``–``com9``, ``lpt1``–``lpt9``, ...) are rejected.
- **Casefold collisions.** Two names that differ only by case are rejected.
- **Duplicate directories.** Two profiles may not map to the same ``directory``.
- **Default must exist.** ``default_profile`` must name a defined profile.
- **At least one profile.** The registry must define at least one profile.
- **Schema version.** Only ``schema_version = 1`` is supported.
- **Executable shape.** ``opencode_executable`` must be a command name or an
  absolute path, not a shell string with spaces.

What errors look like
---------------------

The registry is loaded at startup for every command. A missing registry and an
invalid registry surface differently:

- **Missing registry** (no file) — a management command exits 2 with a clean
  message:

  .. code-block:: console

      $ oc-selector list
      Error: No registry found. Run 'oc-selector init' first.

- **Invalid registry** (a file that fails validation) — the failure is raised
  while the registry is loaded in the top-level dispatcher, so it exits 2 with a
  clean ``Error:`` message and no stack trace, for example:

  .. code-block:: console

      $ oc-selector list
      Error: Unknown registry key: 'bogus_key'

  Some failures (unknown key) produce a short message; others (duplicate
  directory, a ``default_profile`` that is not defined, an unsupported
  ``schema_version``) surface as Pydantic's ``1 validation error for Registry``
  text.

The full exit-code behavior is documented in :doc:`exit-codes`.
