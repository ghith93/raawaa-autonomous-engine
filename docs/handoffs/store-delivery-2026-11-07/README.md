# Raawaa-store handoff — 2026-11-07 (delivery contract + PR #158 CSP harness)

**Status:** code complete and verified offline in the engine session's sandbox.
**Blocker:** the Arena GitHub connection can *read* `ghith93/Raawaa-store` but every write
returns `403 Resource not accessible by integration`. This was verified, not assumed:

| Attempt | Result |
|---|---|
| `git push origin arena/hama-free-delivery-20261107` | `403` (remote: Permission to ghith93/Raawaa-store.git denied) |
| `gh pr create --repo ghith93/Raawaa-store` | GraphQL `403 Resource not accessible by integration` |
| `gh api -X POST repos/ghith93/Raawaa-store/git/blobs` | `403 Resource not accessible by integration` |
| `gh api repos/ghith93/Raawaa-store` (`permissions`) | reported `push: true`, which the write calls above contradict |

The engine repository itself accepts writes normally on this same token, so the
limitation is specific to the store repository scope. Nothing was bypassed and no
permission was changed.

Because ChatGPT's connector is blocked the same way (documented in #464 comments
`6700211702` and `6700296584`), the two fixes are delivered here as ready-to-apply
patch files instead of being silently dropped.

## 1. `hama-free-delivery.patch` — P0 delivery contract

Base: `main` @ `e81e25e44a11cc5b5d7746c3d8def7e1d24cb38e`
Commit in this patch: `fix(delivery): make Hama free for every payment method, other governorates $2`

Owner-approved rule implemented:

- Hama governorate → **0$ delivery for COD and Sham Cash alike**
- Other Syrian governorates → **2$ for both payment methods**
- Product prices untouched: 13 / 16 / 25

Why it was a real bug and not just copy: `js/cart.js` derived free delivery only from
`payment.methods[].freeDeliveryIn`, so **COD in Hama was charged 2$** while the page said
free, and `zone.fee` drove every other surface. The fix makes a zero-fee zone free for
every payment method (keeping `freeDeliveryIn` for per-method exceptions) and applies the
same rule in the server pricing engine, the campaign listing and the margin tool.

Apply:

```bash
git clone https://github.com/ghith93/Raawaa-store.git && cd Raawaa-store
git checkout -b arena/hama-free-delivery-20261107 main
git am /path/to/hama-free-delivery.patch
npm ci
npm run gates:offline      # expect: 30 checks, 525 site checks, 260 unit tests, 0 failures
git push -u origin arena/hama-free-delivery-20261107
gh pr create --base main --title "fix(delivery): توصيل حماة مجاني لكل طرق الدفع"
```

Evidence recorded offline: `npm run gates:offline` → `نجح: 30 · فشل: 0`,
`نجح: 525 · فشل: 0`, unit tests `260 pass / 0 fail`. New
`scripts/delivery-contract.test.mjs` pins the four cases
(Hama/COD 0$, Hama/Sham Cash 0$, other/COD 2$, other/Sham Cash 2$) across the browser
cart, the WhatsApp message and the server engine, plus a copy guard that fails if any
page re-states a Hama fee. No deployment and no price change.

## 2. `pr158-csp-axe-harness.patch` — P1 PR #158 CI repair

Base: PR #158 head `5192a8334c68685daca031136e125f4b53b84f89`
Commit in this patch: `fix(ci): load axe as a same-origin external script under the strict CSP`

Root cause confirmed from run `42243949647`: `page.add_script_tag(content=AXE)` injects an
inline script that `script-src 'self' https://connect.facebook.net` blocks.

What the patch does:

- `scripts/axe_loader.py` (new, single implementation): registers a same-origin test-only
  route `/__test__/axe.min.js`, fulfils it from `scripts/vendor/axe.min.js`, injects axe via
  `add_script_tag(url=...)`, and waits for `window.axe`.
- Removes the ineffective `Page.setBypassCSP` CDP call — bypassing the policy would hide the
  very regression PR #158 exists to prevent.
- Wires the loader into `axe-audit.py`, `a11y-audit.py`, `serum-browser-test.py` and
  `campaign-browser-test.py`; each asserts the bundle was delivered externally.
- `assert_inline_scripts_blocked()` adds the requested positive control: an inline probe must
  be rejected, and the check only claims proof when the response actually carried a CSP.
- `scripts/csp-axe-harness.test.mjs` (offline) keeps the CSP strict: no harness may return to
  inline injection, `_headers` must stay `script-src 'self'` without `unsafe-inline`/`unsafe-eval`,
  the test route must stay same-origin and absent from production files, and axe must not be
  copied into `dist`.

Apply:

```bash
git fetch origin pull/158/head:pr158 && git checkout pr158
git am /path/to/pr158-csp-axe-harness.patch
npm ci && npm run gates:offline
git push origin pr158:arena/95bc3a4b-raawaa-store   # the PR #158 branch
```

## What is proven and what is not

| Claim | Evidence | Status |
|---|---|---|
| Delivery rule consistent across cart, WhatsApp message, server pricing, campaign page, docs | `scripts/delivery-contract.test.mjs`, `gates:offline` 260/260 | **verified offline** |
| axe no longer relies on inline injection; CSP stays strict | `scripts/csp-axe-harness.test.mjs`, 5/5 | **verified offline** |
| axe actually executes in Chromium and WebKit under the new CSP | needs Site CI on the PR head | **not verified** — Playwright browser binaries cannot be downloaded in this sandbox (`playwright install chromium` fails), so no claim is made |
| Cloudflare Pages preview behaves like the static server for CSP | needs the PR preview | **not verified** |
| Live site serves 0$ Hama delivery | needs deploy | **not verified**, and no deploy was performed |

## Remaining owner/CI actions

1. Apply either or both patches with a token that has write access to `ghith93/Raawaa-store`.
2. Run exact-head Site CI on both branches; the browser jobs are the only unproven part.
3. Do not merge PR #136 (stale Hama COD 2$ rule) or PR #157 as-is.
4. Keep the homepage/visual direction untouched until the owner approves it; neither patch
   changes any visual design.
