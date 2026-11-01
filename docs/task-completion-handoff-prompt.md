# Reusable Task-Completion Handoff Prompt

Copy and send this prompt after a task or whenever you want work to continue:

---

Continue the existing Raawaa Autonomous Engine project from the **latest verified repository state**. Do not start over, rebuild existing components, or treat this message as a new project.

1. Inspect the current main branch, recent commits, open issues and PRs, exact CI/workflow results, tests, and the project's current operating notes before changing anything.
2. Read and follow the project's Continuous Improvement and Research Charter together with the existing architecture and policy. The existing architecture and safety/cost gates take precedence; the charter is additive and must not create conflicts or duplicate systems.
3. Verify what was actually completed in the previous cycle. Do not rely on an earlier conversational claim if the repository or CI does not confirm it.
4. Select the highest-impact safe and unblocked task. Research, implement, test, review, document, and verify it. Do not stop at planning or merely give recommendations.
5. Reuse existing agents, tools, workflows, memory, schedulers, quota controls, and integrations wherever possible. Improve them incrementally.
6. Continue to the next safe task after each completed task. Do not wait for approval for routine technical decisions or ordinary code/test/documentation work.
7. Preserve zero-spend defaults, dry-run behavior, disabled external writes, provider quota protections, and all approval gates. Do not enable live inference, paid services, external side effects, production deployment, or access changes unless the existing policy and required explicit approval permit them.
8. Use only permissions and integrations actually available. Never expose secrets or ask me to repeat a setup that is already verified. If a genuine external blocker remains, specify the exact action and minimum permission needed, then continue independent work.
9. For every change, use an isolated branch and appropriate tests. Inspect the diff and verify CI against the exact PR head SHA. Do not merge untested or failing changes.
10. Update durable project documentation or the relevant issue with the verified result, links, current limitations, and next priority. Distinguish implemented, tested, merged, deployed, and runtime-verified states.
11. End with a concise checkpoint: what changed, evidence/links, tests and CI status, blockers (if any), and the next task. Then keep going if another safe task can be completed.

**Standing instruction:** Treat this as authorization to continue the existing work autonomously within the project's already granted permissions and safety policy—not as authorization to spend money, bypass safeguards, expose secrets, or perform reserved external actions.

---

