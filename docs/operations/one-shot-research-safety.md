# One-shot public research: disabled pending evidence

The Task Runner's `run_public_research_once` input is retained for compatibility,
but explicitly fails before checkout or network/task operations. The workflow no
longer synthesizes a fresh provider-verification timestamp, enqueues a research
task, or enables live inference. Scheduled and ordinary manual batch runs remain
subject to the existing policy gates. The obsolete push-triggered smoke enqueue
was removed so edits to this workflow do not automatically mutate production D1.

A future pilot needs independently verified current account tier and model
eligibility, account-side zero-cost hard-stop evidence, current policy/terms and
quota evidence, secret presence without disclosure, and an isolated task claim
that cannot consume an unrelated concurrent task. A snapshot of an idle queue is
not an atomic exclusion. Verify that only the approved route can execute and that
failure/unknown outcomes cannot cause unsafe retries. Capture provider usage and
billing evidence independently; a configured zero price is not billing proof.
Treat this as a new reviewed implementation, not a switch to bypass the guard.

## Sandbox evidence boundary

The manual gVisor POC, if run successfully, provides only the specific positive
and negative controls actually executed. No passing live run is asserted here.
The existing POC does not establish host parent-environment isolation, full
process-tree cleanup on timeout, lease-loss fencing, or all #440 criteria.
The Codespaces executor remains disabled until the applicable containment,
resource, timeout/cleanup and lease-fencing tests pass. A green unit test or a
partial POC does not authorize execution of arbitrary repository code.
