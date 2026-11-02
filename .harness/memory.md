# Raawaa Engine workspace memory

- Target repository: ghith93/raawaa-autonomous-engine only. Do not modify Raawaa-store.
- Zero-spend default: no paid API, paid subscription, paid fallback, or automatic billing.
- Keep production writes, customer actions, deployment, D1 migrations and secret access behind existing explicit policy gates.
- AndroidHarness is optional for operator-initiated diagnostics only; it is not an execution worker, scheduler, or release gate. GitHub Actions/cloud infrastructure remain the normal control plane.
- Use app-UID workspace access by default. Root and Shizuku are not prerequisites.
- Never store credentials or customer-sensitive data in workspace memory.
