OMO separation
==============

This page explains why the OMO config lives at ``~/.omo`` — independent of the
selected profile directory — and exactly what ``sync`` and ``upgrade-omo`` do
and do not touch.

Why ``~/.omo`` is independent of the selected profile
-----------------------------------------------------

Profile selection works by pointing OpenCode at a directory: the launcher
always sets ``OPENCODE_CONFIG_DIR`` to the exact directory of the selected
profile. That is the *only* thing selection changes about where OpenCode looks
for its configuration.

The OMO config is a different kind of resource. It is a single, per-user file
at the actual home directory — ``~/.omo`` — resolved from ``Path.home()`` and
deliberately **not** from ``XDG_CONFIG_HOME``. Moving it under a profile
directory would make switching profiles switch the OMO config too, which is
not the intent: the OMO config is shared across all of a user's profiles, and
profiles do not *replace* it, they *select within it*.

That selection happens through the environment, not through paths:

- A profile that declares an ``omo_profile`` is *owned*: the launcher sets
  ``OMO_PROFILE`` to the registered value and clears ``OCX_PROFILE``.
- A profile without one is *unmanaged*: the launcher preserves whatever
  ``OMO_PROFILE`` and ``OCX_PROFILE`` were inherited, unchanged.

So the separation is: the profile directory decides which OpenCode settings
apply, and the environment decides which OMO profile applies. The file itself
stays put at ``~/.omo`` in both cases.

What ``sync`` touches
---------------------

``sync [PROFILE] [--all]`` operates on the **profile directory** — the
directory registered for the target profile under the config parent. For each
target it:

1. Detects the host package in that directory: a ``package.json`` means npm,
   a ``pyproject.toml`` means pip; neither means "no host package detected"
   and the sync fails for that profile.
2. If a target version was given and it already matches the current version,
   the sync is a no-op (``Already at target version``).
3. Otherwise it runs the package manager's install command for that directory,
   inside a journaled transaction, and reports the resulting version.

``sync`` does **not** touch ``~/.omo``, and it does **not** touch the registry
file. Its write surface is the target profile directory (plus the state root's
transaction machinery).

What ``upgrade-omo`` touches
----------------------------

``upgrade-omo [PROFILE] [--version VERSION] [--all]`` operates on **``~/.omo``
only**. It:

1. Selects the eligible profiles — those that declare an ``omo_profile`` —
   from the registry (read-only).
2. Reads the current ``~/.omo`` config; a missing or invalid file is a clean
   exit 2.
3. Resolves the target version: the explicit ``--version`` if given, otherwise
   the version probed from the registered OpenCode executable, which must be a
   verified 1.x release.
4. Builds the new config as the current config with its ``version`` field set
   to the target, validates it against the schema, and writes it through a
   journaled transaction.

``upgrade-omo`` does **not** touch any profile directory and does **not**
touch the registry file. Its write surface is ``~/.omo`` (plus the state
root's transaction machinery).

The two commands are complementary and disjoint: ``sync`` manages what is
*inside* a profile directory, ``upgrade-omo`` manages the shared ``~/.omo``
file, and neither crosses into the other's surface.
