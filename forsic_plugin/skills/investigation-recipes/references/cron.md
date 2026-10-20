# Connect cron configuration to execution

Methodology inspiration: DFIQ Q1062, dfiq_version 1.1.0. Independently written procedure v1.

Use for cron configuration, referenced files, or execution questions. Verify the distribution/cron implementation; read only needed original files in `/etc/crontab`, `/etc/cron.d/`, and relevant user spools. Do not claim nonexistent standard paths were read successfully. systemd timers and at are separate mechanisms.

Preserve file paths/hashes/line coordinates and user/schedule/command/environment variables. Distinguish user-field differences in system and user crontabs. Interpret timezone, daemon configuration, anacron, and run-parts call chains only with actual configuration. Statically read target commands/scripts; do not execute them.

Configuration alone supports scheduled behavior. If execution matters, compare available CRON/syslog/journal logs, process traces, and output files in the selected time window. Record collection/retention scope and failures. File mtime is not execution time; a command-start log is not completion/success. authlog-only inspection cannot exclude all cron execution. Missing logs alone establish neither nonexecution nor concealment.
