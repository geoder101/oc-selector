Sync the host package
=====================

Goal
----

We want to bring a profile's host package (its ``package.json`` or
``pyproject.toml``) up to date.

Preview first
-------------

We preview the sync with ``--dry-run``, which reports what would happen without
running the package manager:

.. code-block:: console

    $ oc-selector sync work --dry-run
    work: Dry run: would sync my-workspace to latest

Run the sync
------------

We run the real sync:

.. code-block:: console

    $ oc-selector sync work
    work: Synced to 1.0.0

Sync all profiles
-----------------

We can sync every profile at once with ``--all``. Profiles without a host
package report an error, and the command exits non-zero if any of them fail:

.. code-block:: console

    $ oc-selector sync --all --dry-run
    default: No host package detected (No package.json or pyproject.toml found)
    work: Dry run: would sync my-workspace to latest
    personal: No host package detected (No package.json or pyproject.toml found)
