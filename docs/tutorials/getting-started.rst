Getting started
===============

In this tutorial, we install oc-selector, initialize it, create our first
profile, set it as the default, and run a command that forwards to the native
OpenCode executable. By the end, we have a working setup we can use every day.

What we will build
------------------

A registry with one profile (``work``) that points at an OpenCode settings
directory and pins a model, set as the default so that a bare ``oc-selector``
invocation picks it up.

Install
-------

We install oc-selector with pip or uv:

.. code-block:: console

    $ pip install oc-selector
    # or
    $ uv tool install oc-selector

Initialize
----------

We initialize the configuration and registry. This creates the config directory
and an empty registry with a default profile:

.. code-block:: console

    $ oc-selector init
    Created config directory: /tmp/opencode/home/.config
    Created registry: /tmp/opencode/home/.config/oc-selector/config.toml

The config lives under ``$XDG_CONFIG_HOME`` (default ``~/.config``); the paths
above are from the sandbox we ran this in.

Create a profile
----------------

We create a profile named ``work`` that points to ``work-dir`` and uses the
``openai/gpt-4`` model:

.. code-block:: console

    $ oc-selector create work work-dir --model openai/gpt-4
    Created profile 'work'

Set the default
---------------

We set ``work`` as the default profile:

.. code-block:: console

    $ oc-selector set-default work
    Set default to 'work'

List profiles
-------------

We list all registered profiles. The ``*`` marks the default:

.. code-block:: console

    $ oc-selector list
      default -> default
    * work -> work-dir (openai/gpt-4)

Run with forwarded args
-----------------------

Now we run a command. Everything after the profile selection is forwarded to the
native OpenCode executable byte-for-byte:

.. code-block:: console

    $ oc-selector --model openai/gpt-4 "fix the bug"
    opencode: --model openai/gpt-4 fix the bug

We are done. We have a working setup with a default profile that forwards args
to OpenCode. Next, we take it through the full lifecycle in
:doc:`full-lifecycle`.
