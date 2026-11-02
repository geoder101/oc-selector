Glossary
========

Terms used throughout the oc-selector documentation.

profile
    A named settings profile in the :doc:`registry <registry>`. A profile maps a
    name to a settings directory (relative to the config parent) and may pin a
    model and associate an OMO profile. Profiles are created with ``create`` and
    changed with ``edit``.

registry
    The single TOML file at ``$XDG_CONFIG_HOME/oc-selector/config.toml`` that
    records the profiles, the default profile, and the native executable. See
    :doc:`registry <registry>` for the full file format.

OMO config
    The OpenCode Model Operator config at ``~/.omo``. It always lives in the
    actual home directory, independent of the selected OpenCode settings
    directory. ``upgrade-omo`` updates it to a target version.

host package
    The package that ``sync`` installs into a profile's settings directory. It is
    managed through the local node/npm toolchain; the sync is exact and
    idempotent, with no network access.

journal
    A record of a pending transaction, stored under the state root's
    ``journals/`` directory. A journal tracks the operation, its phase, and the
    pre- and post-images of the files it touches, so an interrupted mutation can
    be recovered.

transaction
    An atomic, lock-guarded mutation of the registry or state. Every mutation
    runs under an exclusive lock at the state root and is journaled through a
    state machine (prepared, applying, committed, cleanup-complete) so it can be
    rolled back or recovered.

state root
    The directory ``$XDG_CONFIG_HOME/oc-selector``. It holds the registry, the
    backups, the journals, and the maintenance lock. It is the sibling of the
    registry file's parent.

default profile
    The profile selected when no profile name is given on the command line. It is
    recorded in the registry's ``default_profile`` key and set with
    ``set-default``.
