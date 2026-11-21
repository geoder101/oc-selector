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
   SHA-256 of its preimage, and the SHA-256 of its intended postimage before
   application begins. Backups are private, flushed files under
   ``backups/<transaction-id>/``, keyed by the complete destination path.
- Journal and destination writes flush a temporary sibling before atomically
  replacing the previous file. Parent directories are fsynced where supported.
  A failed write therefore preserves the previous complete journal or file.
- The committed state is flushed to the journal **before** any rollback
  material is deleted. Recovery therefore always has something to work from.

A journal is a durable receipt: it says what the transaction intended, what it
had already done, and how to get back to the pre-transaction state.

The phase state machine
-----------------------

A journal moves through exactly four phases, defined by the ``JournalPhase``
enum in ``transactions/journal.py``:

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
  staged files are being atomically published at the destinations. A crash here
  means some destinations may already hold the new content.
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

1. Recovery acquires the maintenance lock, then reads and validates the journal.
   Unreadable journals block recovery and launch admission rather than being
   treated as a clean state.
2. For prepared or applying transactions, all current resources must match a
   recorded preimage or postimage, and all required backups must validate.
   Recovery then restores original files and symlink targets and removes
   newly created resources. Unexpected edits or missing backups cause refusal
   while preserving the journal and evidence.
3. Committed transactions keep their verified postimages and finish cleanup.
4. The journal file is atomically rewritten with phase ``cleanup-complete``, so a second
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
| In **committed** (after apply,       | Verify the installed postimages, finish       |
| before cleanup)                      | staging cleanup, and close the journal.       |
| Journal already **cleanup-complete** | Not pending; ignored.                         |
| Journal file corrupt                 | Refuse and preserve the recovery evidence.    |
+--------------------------------------+-----------------------------------------------+

The committed journal is the durable commit point. Recovery preserves that
decision, while an interrupted applying transaction is rolled back.
