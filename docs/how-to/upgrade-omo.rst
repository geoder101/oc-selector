Upgrade the OMO config
======================

Goal
----

We want to bring the OMO config at ``~/.omo`` up to a target version. The
profile must declare an OMO profile (set with ``omo_profile`` in the registry);
a profile without one is not eligible.

Preview first
-------------

We preview the upgrade with ``--dry-run``:

.. code-block:: console

    $ oc-selector upgrade-omo work --dry-run
    Dry run: would upgrade from 1.0.0 to 1.2.3

Run the upgrade
---------------

We run the real upgrade. Without ``--version``, the target is the host
OpenCode's version:

.. code-block:: console

    $ oc-selector upgrade-omo work
    Upgraded from 1.0.0 to 1.2.3

Pin an explicit version
-----------------------

We can pin a specific target version:

.. code-block:: console

    $ oc-selector upgrade-omo work --version 1.5.0
    Upgraded from 1.2.3 to 1.5.0
