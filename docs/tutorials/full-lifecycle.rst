The full lifecycle
==================

This tutorial continues from :doc:`getting-started`. We already have a registry
with a default ``work`` profile. Here we take a profile through its whole life:
we add a second profile, sync its host package, upgrade its OMO config, check
diagnostics, install shell completion, and recover from an interrupted
transaction.

Add a second profile
--------------------

We create a second profile, ``personal``, that points to ``personal-dir``:

.. code-block:: console

    $ oc-selector create personal personal-dir
    Created profile 'personal'

Sync the host package
---------------------

A profile's directory holds a host package (a ``package.json`` or
``pyproject.toml``). ``sync`` brings that package up to date. We preview first
with ``--dry-run``, which reports what would happen without running the package
manager:

.. code-block:: console

    $ oc-selector sync work --dry-run
    work: Dry run: would sync my-workspace to latest

We are happy with the preview, so we run the real sync:

.. code-block:: console

    $ oc-selector sync work
    work: Synced to 1.0.0

Upgrade the OMO config
----------------------

A profile can declare an OMO profile (set with ``omo_profile`` in the registry).
``upgrade-omo`` brings the OMO config at ``~/.omo`` up to the host's version:

.. code-block:: console

    $ oc-selector upgrade-omo work
    Upgraded from 1.0.0 to 1.2.3

Check diagnostics
-----------------

We run ``info`` to see the health of the whole setup. Each profile reports
``ok`` when its directory exists:

.. code-block:: console

    $ oc-selector info
    OpenCode: 1.2.3
    Default profile: work
    Profiles: 3
      default: ok
      work: ok
      personal: ok

Install shell completion
------------------------

We install completion for one shell (bash) by generating the script and eval-ing
it. Add the equivalent line to your shell's startup file to make it permanent:

.. code-block:: console

    $ eval "$(oc-selector completion bash)"

Recover from an interrupted transaction
---------------------------------------

Every mutation runs inside a journaled transaction. If one is interrupted
(crash, Ctrl-C mid-write), a pending journal is left behind. We recover it with
``init --recover``:

.. code-block:: console

    $ oc-selector init --recover
    Recovered: 9415e1ec-d8fb-4e4f-9d83-50ec72df7f01

Running it again confirms there is nothing left to recover:

.. code-block:: console

    $ oc-selector init --recover
    No pending recoveries.

We are done. We have taken a profile through its full lifecycle and left the
registry in a clean, recoverable state.
