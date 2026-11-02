Choose the default profile
==========================

Goal
----

We want a bare ``oc-selector`` invocation to pick a specific profile, so we set
it as the default.

Set the default
---------------

We set ``personal`` as the default profile:

.. code-block:: console

    $ oc-selector set-default personal
    Set default to 'personal'

Preview with ``--dry-run``
--------------------------

We preview a change of default without writing:

.. code-block:: console

    $ oc-selector set-default work --dry-run
    Dry run: would set default to 'work'

Confirm with ``list``
---------------------

We list the profiles. The ``*`` marks the current default:

.. code-block:: console

    $ oc-selector list
      default -> default
      work -> work-dir (anthropic/claude-3)
    * personal -> personal-dir
