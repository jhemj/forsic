# Narrow referenced targets from Windows Run values

Methodology inspiration: DFIQ Q1065, dfiq_version 1.1.0. Independently written procedure v1.

Use for autostart configuration or Run values referencing specific files. Selectively query `Software\Microsoft\Windows\CurrentVersion\Run` in SOFTWARE and relevant users' NTUSER.DAT. If the question includes RunOnce/32bit views, check actual presence of those separate keys. Do not generalize selected-key findings to all autostart mechanisms.

Follow inspect-artifacts windows.md to record hive, SID, keys/values, types, raw strings, and last-write. Statically interpret environment variables, quotes, arguments, and interpreters to record referenced paths/call conditions. Do not execute commands. Treat ambiguous unquoted paths and missing environment variables as interpretation limits.

Compare referenced-file presence/hashes/script source with discriminating actual login/process traces. A value's presence observes configuration. Execution, success, user attribution, and maliciousness need further evidence. Do not turn key last-write into individual-value creation time. Compare legitimate installation/administration explanations and counterevidence within the current question's scope.
