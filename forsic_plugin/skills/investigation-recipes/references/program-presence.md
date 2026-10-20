# Distinguish program presence, installation, and execution

Methodology inspiration: DFIQ Q1008, dfiq_version 1.1.0. Independently written procedure v1.

Use for current/past installed programs or a specific executable's presence. On Windows, select SOFTWARE Uninstall entries, Amcache, and actual files; on Linux, local distribution package databases, installation logs, and actual files. Do not execute tools/package managers or connect to networks to refresh state.

Fix host, user, time, and source-generation conditions; link paths, versions, identifiers, and hashes rather than names alone. Read inspect-artifacts windows.md for detailed Windows parsing as needed. Distinguish residual inventory after deletion, portable programs, per-user installations, and other volumes.

Report file presence, inventory registration, and installation records as separate observations. For execution questions, select follow-up PF, process logs, or execution outputs. Do not use Amcache link_date as execution time. If only some databases/users were inspected, record scope/failures; do not extend absence to the whole computer.
