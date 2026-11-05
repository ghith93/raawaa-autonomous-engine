# Operator action guide

Telegram integration was cancelled by the owner on 2026-11-05. Its setup and repair workflow has been removed. Do not provision or reactivate it. Remote webhook/credential revocation is not implied by repository deletion.

## Other active safety boundaries

- Sandbox issue #440: Codespace execution remains release-disabled. A validator
  test or CI run is not a live sandbox escape proof. Do not run the adversarial
  live workflow until its exact changes and provenance have been independently
  reviewed.
- Model providers: remain disabled unless account eligibility, terms, hard spend
  limits and the existing inference gates are verified. No live inference, paid
  fallback or usage with uncertain cost is authorized by this playbook.
- Financial/market execution remains disabled. No wallet signing, orders, live
  market calls, swaps or other financial actions are authorized.
