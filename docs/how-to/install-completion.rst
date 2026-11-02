Install shell completion
========================

Goal
----

We want tab completion for ``oc-selector``. We install it for one shell; the
same pattern works for the others.

Bash
----

We generate the script and eval it. Add the line to ``~/.bashrc`` to make it
permanent:

.. code-block:: console

    $ eval "$(oc-selector completion bash)"

Zsh
---

.. code-block:: console

    $ eval "$(oc-selector completion zsh)"

Fish
----

.. code-block:: console

    $ oc-selector completion fish | source

PowerShell
----------

.. code-block:: console

    $ oc-selector completion powershell | Out-String | Invoke-Expression

Each ``oc-selector completion <shell>`` prints a self-contained script for that
shell; the ``eval``/``source``/``Invoke-Expression`` step installs it into the
current shell.
