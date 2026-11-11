# XingLi MO2 Assistant v1.8.3 — Single MOD Toggle Hotfix

- Fixed MO2 2.4.4 single-MOD enable/disable path: one MOD now always calls `IModList.setActive(QString, bool)` instead of the `QStringList` overload.
- Interactive MOD Manager writes use `verify=False` so they do not immediately re-enter MO2 state reads while VFS re-evaluation is in progress.
- Removed automatic full-table reload after enable/disable. The affected rows update locally and only receive a lightweight delayed state verification.
- Added before/after diagnostics around MOD Manager state writes so a future freeze can be localized to the C++ `setActive` boundary.
- Existing MO2 2.4.4 safety rules remain: no `pluginList()` state callbacks, no `onModStateChanged()`, no worker-thread IOrganizer calls.
