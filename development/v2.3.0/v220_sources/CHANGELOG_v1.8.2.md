# XingLi MO2 Assistant v1.8.2 — Stability Foundation

Target runtime: Mod Organizer 2.4.4 / Python 3.8 / PyQt5.

## Central ModState authority
- Added `mod_state_service.py` as the only XingLi module that interprets MO2 `ModState` flags.
- Centralized ACTIVE / ESSENTIAL / VALID compatibility fallbacks.
- Centralized batch `setActive()` with no-op suppression, bulk-call preference, per-MOD fallback and post-write verification.
- Centralized MOD records (internal name, display name, active, priority, essential, valid, managed path).
- Migrated Mod Manager, LOD Pipeline, Mod Compressor, Mod Ops Assistant, Auto Resolution and DSD active-MOD reads to the shared service.

## MO2 2.4.4 compatibility foundation
- Added automatic source compatibility audit during plugin initialization.
- Audit rejects runtime calls to known hazardous APIs: `pluginList()`, `onPluginStateChanged`, `onModStateChanged`, `onPluginMoved`, and `waitForApplication()`.
- All source files are checked against Python 3.8 grammar.
- No plugin-list state callback was introduced.

## Unified diagnostics
- Added `stability.py` with a rotating XingLi log.
- Runtime compatibility information and source-audit results are written to `pluginDataPath/xingli_assistant/logs/XingliAssistant.log` when available.
- Mod Manager, LOD Pipeline and controller-level exceptions now use the shared logger.

## Regression protection
- MOD enable-state semantics are no longer reimplemented independently in multiple modules.
- Existing LOD checkpoint behavior and v1.8.1 Mod Manager features are preserved.
- No `.pyc` or `__pycache__` files are distributed.
