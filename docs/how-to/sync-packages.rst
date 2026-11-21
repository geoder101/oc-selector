Sync the host package
=====================

Goal
----

We want to bring a profile's host package (its ``package.json`` or
``pyproject.toml``) up to date.

For npm, the profile's ``package.json`` declares exactly one host dependency:

.. code-block:: json

    {
      "name": "my-workspace",
      "dependencies": {
        "my-host-package": "1.0.0"
      }
    }

``sync`` updates ``my-host-package`` and verifies its installed version; the
workspace's own name and version are not an install target. Python profiles
declare their distribution name in ``[project]`` in ``pyproject.toml``.
Python installs go into the profile's ``python-packages`` directory, using the
same interpreter as the wrapper, rather than modifying its global environment.

Preview first
-------------

We preview the sync with ``--dry-run``, which reports what would happen without
running the package manager:

.. code-block:: console

    $ oc-selector sync work --dry-run
    work: Dry run: would sync my-host-package to latest

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
    work: Dry run: would sync my-host-package to latest
    personal: No host package detected (No package.json or pyproject.toml found)
