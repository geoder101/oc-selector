Create and edit a profile
=========================

Goal
----

We want to add a profile to the registry, and later change one of its fields
without touching the others.

Create a profile
----------------

We create a profile named ``work`` that points to ``work-dir`` and pins a model:

.. code-block:: console

    $ oc-selector create work work-dir --model openai/gpt-4
    Created profile 'work'

A profile does not need a model:

.. code-block:: console

    $ oc-selector create personal personal-dir
    Created profile 'personal'

If you omit the directory, the profile defaults to the oc-selector state root
at ``profiles/<name>`` (``~/.config/oc-selector/profiles/<name>`` by default).
Pass a directory to point elsewhere, or ``--no-interactive`` to skip the prompt
and take the default:

.. code-block:: console

    $ oc-selector create fast --no-interactive
    Created profile 'fast'

Preview with ``--dry-run``
--------------------------

We preview a create without writing anything:

.. code-block:: console

    $ oc-selector create temp temp-dir --dry-run
    Dry run: would create profile 'temp' -> temp-dir

Name rules
----------

Profile names must match ``[a-z][a-z0-9-]{0,47}`` (lowercase, starting with a
letter). An invalid name is rejected with exit code 2:

.. code-block:: console

    $ oc-selector create BadName dir
    Error: Invalid profile name: 'BadName' (must match [a-z][a-z0-9-]{0,47})

Reserved names (the management commands and native OpenCode subcommands) are
rejected too:

.. code-block:: console

    $ oc-selector create list dir
    Error: Profile name 'list' is reserved

Edit a profile
--------------

We change a field of an existing profile. Only the fields we name change; the
rest are preserved:

.. code-block:: console

    $ oc-selector edit work --model anthropic/claude-3
    Edited profile 'work'

We preview an edit without writing:

.. code-block:: console

    $ oc-selector edit work --directory other-dir --dry-run
    Dry run: would edit profile 'work' -> directory=other-dir, model=anthropic/claude-3

Associate an OMO profile
------------------------

A profile can carry an OMO profile name with ``--omo-profile``. We set it at
create time:

.. code-block:: console

    $ oc-selector create omo omo-dir --omo-profile standard
    Created profile 'omo'

Or change it later with ``edit``:

.. code-block:: console

    $ oc-selector edit omo --omo-profile minimal
    Edited profile 'omo'

Delete a profile
----------------

We remove a profile from the registry. The settings directory and any OMO
config are left untouched:

.. code-block:: console

    $ oc-selector delete old-profile
    Deleted profile 'old-profile'

Two refusals (exit 1):

- You cannot delete the current default profile. Set a different default first:

  .. code-block:: console

      $ oc-selector delete work
      Cannot delete default profile 'work'; run 'oc-selector set-default <other>' first.

- You cannot delete the last remaining profile:

  .. code-block:: console

      $ oc-selector delete default
      Cannot delete the last remaining profile 'default'.
