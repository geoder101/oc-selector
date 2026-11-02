Transactions
============

This page explains why ``oc-selector`` journals its mutations, what the journal
state machine looks like, how the lock discipline works, and what
``init --recover`` does when a transaction is interrupted.

Why journals exist
------------------

The registry and the OMO config are small files that the user may have spent
time arranging. A mutation that dies halfway — a crash, a power loss, a Ctrl-C
mid-write — must not leave a half-written file behind with no way back.

So every file mutation runs inside a journaled transaction:

- The new content is staged in a private directory first; the destination is
  only touched at commit time.
- A journal file records, for every resource, whether it existed before, the
  SHA-256 of its preimage, and (after commit) the SHA-256 of its postimage.
- The journal is written with a flush and an fsync — and the parent directory
  is fsynced too — so the journal on disk is never ahead of what the OS has
  actually persisted.
- The committed state is flushed to the journal **before** any rollback
  material is deleted. Recovery therefore always has something to work from.

A journal is a durable receipt: it says what the transaction intended, what it
had already done, and how to get back to the pre-transaction state.

The phase state machine
-----------------------

A journal moves through exactly four phases, defined by the ``JournalPhase``
enum in ``transactions.py``:

.. code-block:: text

    begin()                 commit()                          commit()
      |                       |                                  |
      v                       v                                  v
  +-----------+  stage   +-----------+  apply staged   +-------------+  flush   +--------------------+
  | prepared  | --------> | applying  |  files, record |  committed   | journal |  cleanup-complete  |
  +-----------+           +-----------+  postimages    +-------------+ -------> +--------------------+
      |                       |                                  |
      +-----------------------+----------------------------------+
                           |
                           v
                    rollback() or crash:
                    restore preimages,
                    remove created files,
                    release the lock

- **prepared** — the lock is held and the journal exists, but no destination
  file has been touched yet. Staging happens in this phase.
- **applying** — the journal has been rewritten to say "applying", and the
  staged files are being copied over the destinations. A crash here means some
  destinations may already hold the new content.
- **committed** — every destination holds the new content, the postimage
  hashes are recorded, and the journal has been flushed to say "committed".
  Only the private staging directory remains to be cleaned up.
- **cleanup-complete** — staging is gone and the journal is closed. A journal
  in this phase is *not* pending; recovery ignores it.

The journal file itself is rewritten (flush + fsync) at every phase
transition, so the phase on disk is always the last thing the transaction
finished doing.

Lock discipline
---------------

One exclusive ``portalocker`` lock at ``<state root>/maintenance.lock``
protects every mutation. The lock file is created with mode 0600 and the state
root with mode 0700, so the transaction machinery is private to the user.

Acquisition is bounded: the transaction attempts to take the exclusive lock
and, if another process already holds it, fails immediately with a
maintenance error rather than waiting indefinitely. There is no silent
queueing — a second ``oc-selector`` mutation started while the first is in
flight gets a clear error, not a race.

The lock is released exactly once, at the end of ``commit`` or ``rollback``.
Because the journal is flushed before the lock is released, a reader that
acquires the lock afterwards always sees a consistent journal.

What ``init --recover`` does
----------------------------

``init --recover`` (see :doc:`/how-to/recover-transaction`) scans the state
root's ``journals/`` directory for **pending** journals — every journal whose
phase is anything other than ``cleanup-complete`` — and recovers each one:

1. The journal is read and validated; a journal that cannot be parsed is
   skipped (it is not treated as pending).
2. For every resource that **existed before** the transaction and has a
   recorded preimage, the backup copy stored alongside the journal is restored
   over the destination.
3. The journal file is rewritten with phase ``cleanup-complete``, so a second
   run of ``init --recover`` reports ``No pending recoveries.``

Recovery is deliberately conservative: it restores what the journal proves
existed before, and it never invents content. Each journal is reported
individually — ``Recovered: <id>`` on success, ``Failed: <id> - <error>`` on
failure — so one bad journal does not hide the others.

Crash scenarios and their outcomes
----------------------------------

+--------------------------------------+-----------------------------------------------+
| Where the crash lands                | What ``init --recover`` does                  |
+======================================+===============================================+
| Before the journal is written        | Nothing to recover: no journal exists.        |
| In **prepared** (staging only)       | No destination was touched; preimages are     |
|                                      | restored (a no-op if nothing was applied) and |
|                                      | the journal is closed.                        |
| In **applying** (partial apply)      | Destinations that already hold new content    |
|                                      | are restored to their preimages; the          |
|                                      | journal is closed.                            |
| In **committed** (after apply,       | The applied change is rolled back to its      |
| before cleanup)                      | preimage and the journal is closed. A         |
|                                      | transaction that reached committed but did    |
|                                      | not finish cleanup is treated as incomplete.  |
| Journal already **cleanup-complete** | Not pending; ignored.                         |
| Journal file corrupt                 | Not readable, not pending; ignored.           |
+--------------------------------------+-----------------------------------------------+

The design choice to make even a *committed-but-not-cleaned-up* journal
recoverable-by-rollback is deliberate: the alternative (guessing that a
crashed transaction "probably finished") would silently keep a change whose
completion was never proven. ``oc-selector`` would rather redo a mutation than
keep an unproven one.
