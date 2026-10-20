# Security and deployment boundaries

Forsic handles potentially sensitive evidence. Keep source code separate from
operator state and evidence. Publish only a reviewed source export produced by
`deploy_forsic.py export`; an export allowlist does not detect every possible secret.

- Keep `state/`, configuration with real values, API keys, credentials, case files,
  reports, logs, screenshots, backups and installed runtimes out of public source.
  Use synthetic fixtures for tests. Do not attach real evidence to public issues.
- The Linux host runs the application and Hermes runtime. Docker isolates the
  supported analysis tools with read-only evidence mounts; the application itself
  is not containerized. Host tools and Docker permissions remain host privileges.
- Default access is localhost. Trusted private-LAN no-login shares the operator's
  privileges and uses plain HTTP unless independently protected. Use it only on
  an access-controlled private network and never publish or proxy it online.
- Online access requires native authentication, an exact public HTTPS origin,
  a same-host TLS reverse proxy and restricted ingress. See `DEPLOYMENT.txt` and
  `deployment/Caddyfile.example`. Keep the application listener on loopback.
- Authenticated visitors share the operator's workspace. This release provides
  no separate tenant storage, case ownership or per-user role enforcement.
- Configure only approved model/API destinations. External models may receive
  investigation questions, selected evidence and tool results. Model-list checks
  explicitly requested in setup send metadata requests and credentials to the
  selected endpoint. Saving settings alone does not send an inference request.
- Prompt text found inside evidence is untrusted material. Findings and reports
  require analyst review; runtime success does not establish forensic correctness.

Use the pinned Hermes sources and preserve upstream notices. Review source changes,
run relevant offline tests, and restart only at a confirmed idle boundary. Host
network/TLS/login behavior needs deployment-specific verification; packaging and
unit tests do not establish that a new server is ready for internet traffic.

## Reporting a vulnerability

If this repository offers GitHub's private “Report a vulnerability” action under
Security, use it. Otherwise open a public issue asking for a private contact without
including exploit details, tokens, personal data or evidence; wait for a private
reporting channel before sharing sensitive material. Include the release/commit,
a minimal synthetic reproduction, expected and observed behavior, and affected
boundary. Never send case evidence or credentials as part of a vulnerability report.

There is no published support window or response-time guarantee. Until a fix is
reviewed, restrict access to the affected deployment or disable the affected
integration. Do not test a vulnerability against another operator's service.

Sensitive scan boundary: structured offline scans return safe candidate metadata and keep raw
scanner artifacts outside generic shell/source/export access. Never validate credentials online.
User-authorized local integrated report appendices contain bounded original excerpts and exact
locations with omission counts; immutable judgment/reviewer snapshots remain safe. Reports with
these originals are marked local-only and blocked at Telegram discovery and upload. Manual local
browser downloads remain available; treat downloaded reports as sensitive evidence.
