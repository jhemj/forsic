# Connect cron configuration to execution

Methodology inspiration: DFIQ Q1062, dfiq_version 1.1.0. Independently written procedure v1.

Use for cron configuration, referenced files, or execution questions. Verify the distribution/cron implementation; read only needed original files in `/etc/crontab`, `/etc/cron.d/`, and relevant user spools. Do not claim nonexistent standard paths were read successfully. systemd timers and at are separate mechanisms.

Separate discovered paths, read bodies, parsed entries, and interpreted behavior. An empty root spool does not
describe other users' jobs. Service/account names do not establish authorization. For a relevant nonempty user
spool, a small body read or bounded `linux_persistence` pass (`cron_user`) can distinguish hypotheses cheaply;
system crontabs use `cron_system`. Reuse exact retained export selectors. A parser produces configured-entry
candidates, not a maliciousness verdict. If a meaningful discovered file remains unread, preserve that scope
and the reason in the existing question before making an absence claim. Select next checks by value; no need to
inspect every scheduler or every file.

Preserve file paths/hashes/line coordinates and user/schedule/command/environment variables. Distinguish user-field differences in system and user crontabs. Interpret timezone, daemon configuration, anacron, and run-parts call chains only with actual configuration. Statically read target commands/scripts; do not execute them.

Configuration alone supports scheduled behavior. If execution matters, compare available CRON/syslog/journal logs, process traces, and output files in the selected time window. Record collection/retention scope and failures. File mtime is not execution time; a command-start log is not completion/success. authlog-only inspection cannot exclude all cron execution. Missing logs alone establish neither nonexecution nor concealment.
