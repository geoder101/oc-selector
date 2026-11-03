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

What it completes
-----------------

The generated script completes two kinds of input:

- **Management commands and switches.** At the first token it offers the
  management subcommands (``list``, ``create``, ``set-default``, ``completion``,
  ...) alongside the profile names; after a management command it offers that
  command's switches (for example ``oc create --mo<TAB>`` offers ``--model``).
- **The proxied OpenCode app.** At the native boundary — after a bare first
  token, a profile name, or a leading ``--`` — the script forwards the already
  typed words to the OpenCode executable's own completion
  (``<exe> --get-yargs-completions ...``), so ``oc <TAB>`` lists OpenCode's
  subcommands and ``oc run --mo<TAB>`` offers its flags.

The executable the script calls is the one stored in the registry
(``oc-selector list`` shows it); when no registry is installed the default
``opencode`` is used. Because completion is generated once and the registry can
change, re-run ``oc-selector completion <shell>`` after changing
``opencode_executable`` to refresh the baked-in path.

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


Short alias with completion
---------------------------

If you prefer a shorter command name, define an alias and generate completion
for that name with ``--alias``. This drop-in gives you both ``oc`` and
``oc-selector`` with tab completion:

.. code-block:: console

    $ mkdir -p ~/.bashrc.d
    $ cat > ~/.bashrc.d/oc-selector.sh <<'EOF'
    alias oc=oc-selector
    eval "$(oc-selector completion bash)"
    eval "$(oc-selector completion bash --alias oc)"
    EOF

The ``--alias oc`` flag emits a completion script registered under the name
``oc`` rather than ``oc-selector``; the two ``eval`` lines install completion
for both names. The alias only affects interactive bash — for scripts or other
shells, prefer a symlink such as
``ln -s "$(command -v oc-selector)" ~/.local/bin/oc``.
