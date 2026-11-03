---
name: initial-access
description: Compare authentication, service, and file records to investigate possible initial access routes, distinguishing first observation from actual intrusion time.
---
# Narrow initial access routes

The earliest suspicious trace is a starting point, not a confirmed initial intrusion time. Read the current question,
evidence period, and established answers; choose evidence that distinguishes possible entry explanations.

Compare authentication successes/failures and sessions, exposed-service access/error records, account/key changes,
and early file creation/execution traces within the evidence that actually exists. Choose evidence appropriate to
the operating system and services; do not invent paths. Check log rotation, retention start, timezones, and clock
differences. Missing earlier records do not establish absence of earlier intrusion.

- Authentication failure is not successful entry. Success can also be authorized routine administration.
- A vulnerable version or attack string does not establish successful exploitation. Server-side effects or linked
  execution/file traces are needed.
- Autorun settings or malicious files may show persistence or possession without directly proving the initial route.
- Do not link events solely by matching IPs, accounts, or filenames. Record matches and mismatches in sessions,
  hosts, targets, and times.

Link confirmed times to originals through correlate-time and organize related indicators through organize-indicators.
Keep a concise nominal hierarchy: '시점 판단 / 유력 경로 / 근거 / 주요 한계 / 다음 판별'. Seek a useful
qualified bound before returning only 미확인: the earliest supported unauthorized action may imply access no later
than that action, even when entry itself was not logged. Explain that prerequisite and the host/session/clock
linkage in the owning question's inference. A successful login or sudo record alone establishes account activity,
not unauthorized access; report the account-activity period when that is the strongest supported conclusion.
Do not convert no-later-than into strictly-before or infer an earlier calendar day. Report route and time separately
so an unknown route does not erase a supported time bound, and a bounded time does not invent a route.
Run the next discriminating check available through existing tools as a mission; if evidence is missing, report it
as unconfirmed within scope. Do not connect to discovered service addresses or start exploit reproduction or
credential use.

Use scan-sensitive-data for relevant credential traces in selected evidence. Issuance, valid use and intrusion linkage require separate context; never validate credentials online. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
