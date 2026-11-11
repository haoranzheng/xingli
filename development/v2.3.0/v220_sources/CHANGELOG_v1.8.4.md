# XingLi MO2 Assistant v1.8.4 — Grass Checkpoint State-Service Hotfix

- Fixed `PipelineDialog` missing its `ModStateService` binding after the v1.8.2 state-service migration.
- `PipelineDialog` now reuses `PipelineStore.mod_state` as the single state service for LOD stage policies and Grass Cache checkpoint restore/discard paths.
- Added defensive exception handling around checkpoint MOD-state restore so checkpoint cleanup cannot crash the whole dialog.
- Kept MO2 2.4.4 restrictions: no `pluginList()`, `onPluginStateChanged()`, `onModStateChanged()`, or `waitForApplication()` runtime use.
