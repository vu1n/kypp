# Retire `additive-migrations`

Vu asked on 2026-10-04 that ratified decisions stay few and high-level, at the level of app direction. The bar is: a capable agent reading the code would plausibly get it wrong, and the mistake would be costly or hard to undo. This decision falls below that bar.

Change: `status: active` → `superseded`. A local implementation rule. It now lives as a why comment on `_MIGRATIONS` and a Store Gotcha.


---
ratified_rev: 0002
ratified_by: Vu
