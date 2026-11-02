Run diagnostics
===============

Goal
----

We want to see the health of the whole setup: the OpenCode version, the default
profile, and the status of each profile.

Human view
----------

We run ``info``:

.. code-block:: console

    $ oc-selector info
    OpenCode: 1.2.3
    Default profile: personal
    Profiles: 3
      default: ok
      work: ok
      personal: ok

Each profile reports ``ok`` when its directory exists, or ``warning`` with the
specific issues when something is off.

Machine view
------------

We run ``info --json`` for a stable, machine-readable object. The command emits
it on a single line; it is shown here pretty-printed, with the
``recovery.transactions`` list abbreviated:

.. code-block:: json

    {
      "schema_version": 1,
      "wrapper": {"version": "0.1.0"},
      "paths": {
        "config_parent": "/tmp/opencode/home/.config",
        "registry": "/tmp/opencode/home/.config/oc-selector/config.toml",
        "state": "/tmp/opencode/home/.config/oc-selector",
        "omo_config": "/tmp/opencode/home/.omo"
      },
      "default_profile": "personal",
      "opencode": {"path": "opencode", "version": "1.2.3", "status": "ok"},
      "profiles": [
        {"name": "default", "directory": "default", "omo_profile": null, "status": "ok", "issues": []},
        {"name": "work", "directory": "work-dir", "omo_profile": "work-omo", "status": "ok", "issues": []},
        {"name": "personal", "directory": "personal-dir", "omo_profile": null, "status": "ok", "issues": []}
      ],
      "recovery": {"status": "pending", "transactions": ["..."]},
      "lock": {"status": "present", "pid": null, "host": null, "started_at": null},
      "warnings": []
    }

The ``recovery`` field lists the journal files present in the state root. A
committed transaction leaves a ``cleanup-complete`` journal behind, so this
reports ``pending`` whenever any journal file exists; use
:doc:`recover-transaction` to clear a genuinely pending one.
