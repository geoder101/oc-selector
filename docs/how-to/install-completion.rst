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

Make it permanent
-----------------

The one-liners above only affect the current shell session. To make completion
permanent, add the appropriate line to your shell's startup file.

Bash
~~~~

Create a drop-in file that ``~/.bashrc`` sources (the shell sources fragment
directories):

.. code-block:: console

    $ mkdir -p ~/.bashrc.d
    $ printf 'eval "$(oc-selector completion bash)"\n' > ~/.bashrc.d/oc-selector.sh

Or, if you do not use a fragment directory, append the line directly to
``~/.bashrc``:

.. code-block:: console

    $ echo 'eval "$(oc-selector completion bash)"' >> ~/.bashrc

Zsh
~~~

.. code-block:: console

    $ echo 'eval "$(oc-selector completion zsh)"' >> ~/.zshrc

Fish
~~~~

.. code-block:: console

    $ oc-selector completion fish >> ~/.config/fish/config.fish

PowerShell
~~~~~~~~~~

.. code-block:: console

    $ Add-Content -Path $PROFILE -Value 'oc-selector completion powershell | Out-String | Invoke-Expression'
