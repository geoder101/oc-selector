Recover from an interrupted transaction
=======================================

Goal
----

A mutation was interrupted (crash, Ctrl-C mid-write), leaving a pending journal
behind. We want to recover it and return the registry to a consistent state.

What a pending journal looks like
---------------------------------

Every mutation runs inside a journaled transaction. If one is interrupted, a
journal file is left in the state root's ``journals/`` directory, in a phase
other than ``cleanup-complete``:

.. code-block:: json

    {
      "id": "8496aed2-cda6-42f0-91f3-cfecbf7fdd2c",
      "operation": "create",
      "phase": "applying",
      "resources": [],
      "affected_profiles": ["work"],
      "pid": 4242,
      "host": "sandbox",
      "timestamp": "2026-11-02T00:00:00+00:00"
    }

Recover it
----------

We recover the pending journal with ``init --recover``:

.. code-block:: console

    $ oc-selector init --recover
    Recovered: 8496aed2-cda6-42f0-91f3-cfecbf7fdd2c

Idempotency
-----------

Running it again confirms there is nothing left to recover:

.. code-block:: console

    $ oc-selector init --recover
    No pending recoveries.
