OpenCode Selector
=================

A cross-platform command that selects registered OpenCode settings while keeping
normal OpenCode arguments intact.

``oc-selector`` wraps the native ``opencode`` executable. It maintains a registry
of named settings profiles and, on each invocation, selects the registered default
(or an explicitly named profile) and forwards all remaining arguments to the native
command unchanged.

Installation
------------

From a local clone of the repository (run at the repo root)::

    uv tool install .   # recommended: isolated tool environment

Quick start
-----------

.. code-block:: bash

    oc-selector init
    oc-selector create work work-dir --model openai/gpt-4
    oc-selector set-default work
    oc-selector --model openai/gpt-4 "fix the bug"

The :doc:`tutorials <tutorials/index>` walk through a first setup and a full
profile lifecycle, and the :doc:`reference <reference/index>` documents the exact
command grammar, the registry file, and the exit codes.

.. note::

    The generated API reference under :doc:`reference/index` documents the
    internal Python modules of the ``oc_selector`` package. It is **not** a
    supported public API: the only supported interface is the ``oc-selector``
    command-line tool. Internal modules are implementation details and may change
    without notice.

Contents
--------

.. toctree::
   :maxdepth: 1

   tutorials/index
   how-to/index
   reference/index
   explanation/index
