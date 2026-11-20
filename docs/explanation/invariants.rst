Invariants
==========

These are the non-negotiable rules the codebase is built around. Each one is
enforced by the modules that carry it in their documented contracts, and each
has a reason that survives even when the surrounding code changes.

No author constants in installed source
---------------------------------------

The installed package contains no author-specific model IDs, provider names,
recipe resources, or personal setup data. Conversion helpers accept model IDs,
names, and field policies as *parameters*; init creates a generic empty
registry. The reason: the tool is installed on many machines with many
different setups, and a constant baked into the source would silently impose
one author's configuration on every user. Anything user-specific must arrive
through the registry or the command line, where it is visible and editable.

No direct network I/O in the CLI, sync, wizard, or init
-------------------------------------------------------

The management path makes no direct network calls: routing, registry, wizard,
init, recovery, and diagnostics never open a socket or HTTP connection, and
nothing in the wrapper phones home. ``sync`` shells out to a package manager
(``npm install`` or ``pip install``); that command reaches the network to fetch
packages, but the wrapper itself performs no network I/O -- it only invokes a
local package manager. ``info`` runs at most one version-only subprocess with a
short timeout.

The reason: a settings selector runs on the user's machine, often on a machine
with sensitive configuration, and a surprise network call from a tool that is
supposed to be a thin local wrapper is a trust violation. The wrapper never
opens its own socket, issues its own HTTP request, or phones home; predictable,
local behavior is the contract.

No silent fallbacks
-------------------

When something is missing or invalid, the tool says so and stops: a missing
registry is a clean exit 2 with init guidance, an invalid registry is an error,
an unsupported schema version is rejected, an unknown registry key is rejected.
The reason: a silent fallback (guess a default, ignore a bad field, fall back
to a different path) hides the real problem and makes the user's next action
wrong in a way they cannot see. Failing loudly with a machine-readable error
code is always cheaper than debugging a configuration that "almost worked".

A few internal failures are tolerated deliberately rather than surfaced. A
directory ``fsync`` that a platform does not support is skipped, a failure to
resolve the wrapper's own identity in ``_reject_self`` does not block a launch,
a recovery that cannot find a preimage backup still restores the files it can,
and an unparseable OMO config still reports ``from_version`` as ``unknown``.
These are durability, availability, and best-effort-recovery choices about the
wrapper's own bookkeeping, not interpretations of the user's configuration -- the
user-facing boundaries above still fail loudly.

All mutations go through transactions
-------------------------------------

Every write to the registry or the OMO config happens inside a journaled
transaction under the exclusive state-root lock — never as a direct file write
from a command handler. The reason: these are the user's carefully arranged
files, and a crash mid-write must be recoverable. The journal gives every
mutation a durable record of its preimage and a defined recovery path
(:doc:`transactions`), and the lock guarantees two mutations can never
interleave.

No migration of legacy fields
-----------------------------

The tool does not migrate legacy configuration fields into new shapes. Legacy
conversion helpers exist as parameterized, explicitly invoked functions, but
nothing in the normal command path runs them, and unknown fields are rejected
rather than guessed at. The reason: a migration is a lossy interpretation of
someone else's data, and doing it silently would rewrite the user's
configuration based on assumptions the user never made. If a conversion is
wanted, it must be an explicit, visible operation — never a side effect of
``init``, ``sync``, or a profile launch.

No conversion calls in the CLI path
-----------------------------------

The CLI never invokes the legacy-to-native conversion helpers. The reason is
the same as the previous one, stated at the boundary that matters: the command
line is the surface users actually touch, and it must never transform
configuration as a side effect of selecting a profile or running a management
command. Conversion is available as a library capability for explicit use; the
CLI path is selection and forwarding, nothing more.

Idempotent operations
---------------------

Running the same operation twice has the same effect as running it once:
``init`` on an initialized setup reports "Already initialized." and changes
nothing; ``sync`` at the target version is a no-op; ``init --recover`` with no
pending journals reports "No pending recoveries."; ``--dry-run`` writes
nothing at all. The reason: maintenance commands are the ones users re-run
when they are unsure of the state, and a re-run must be safe. Idempotency is
what makes "just run it again" a valid recovery strategy instead of a hazard.
