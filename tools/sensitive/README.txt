Sensitive detector dependency boundary (2026-10-20)

Build preparation only:
  python3 tools/install_forensic_tools.py fetch --cache <non-evidence-cache>
  python3 tools/install_forensic_tools.py install --cache <non-evidence-cache> --prefix <new-build-context>/forensic-tools
The second command is cache-only. The pinned archive, executable, wheel and
configuration SHA256 values live in tools/forensic-tools.lock.json. No scanner
fetches dependencies while examining evidence. Use CPython 3.12 on Linux
amd64/arm64; only amd64 execution is verified here. Full dependency resolution
for presidio-analyzer is deliberately unsupported by this restricted profile.

Real executable: /opt/forensic-tools/bin/gitleaks (symlink into immutable image).
Config: /opt/forensic-tools/sensitive/gitleaks.toml, exact upstream v8.30.1
snapshot; global and per-rule upstream allowlists remain enabled. Rule coverage
and allowed examples must therefore be considered when interpreting no findings.
Never accept evidence-supplied config or CLI flags. Feed only selected bounded
UTF-8 decoded input through stdin, with cwd a trusted private directory containing
no evidence configuration/ignore files. Pass all of:
  stdin --config /opt/forensic-tools/sensitive/gitleaks.toml
  --gitleaks-ignore-path /opt/forensic-tools/sensitive/empty.gitleaksignore
  --ignore-gitleaks-allow --max-archive-depth 0 --max-decode-depth 0
Choose private report/diagnostic sinks; no raw output belongs in model returns.
The dir command automatically loads source/.gitleaksignore even with an explicit
ignore path, as confirmed in pinned cmd/root.go. It is not used by this profile.
No git history, baseline, diagnostics HTTP endpoint or credential verification.
Coordinates in Gitleaks are UTF-8 byte columns for Match, not Secret; validate
against exact decoded content before claiming a precise location. The pinned
location.go counts subsequent line columns relative to the preceding newline
byte index; do not assume every StartColumn follows a conventional text editor.

Presidio API: load this file by an absolute trusted path using importlib:
  /opt/forensic-tools/sensitive/presidio_local.py
  recognizers = module.load_recognizers()
  recognizers[entity].analyze(text=text, entities=[entity], nlp_artifacts=None)
Returns KR_RRN, KR_FRN, KR_PASSPORT, KR_DRIVER_LICENSE, KR_BRN, CREDIT_CARD,
EMAIL_ADDRESS and PHONE_NUMBER. The first six execute unmodified selected
upstream wheel modules. The latter two are explicitly local bounded patterns,
with forsic_provenance='adapter_local_pattern'. Korean phone coverage is limited
to the documented pattern (010 and selected fixed line formats); it is not all
phone assignments or all national dialing plans. Email is ASCII format matching,
not DNS/domain validation. No name, address, Korean NER or person resolution.
AnalyzerEngine, registry, normal package initializer and spaCy are not loaded.
Only regex is imported as an external executable dependency. Exact wheel files
and original dist-info/licenses are retained under sensitive/python. Thirteen
selected source files were checked both against the wheel and pinned upstream
Presidio commit 779dbd286d5ef4d1fbe2514275fb1bce358f2417.

This minimal loader is a Forsic adaptation for a dedicated helper process; it
must fail if ordinary Presidio is already imported. It is not a general-purpose
Presidio replacement. Upstream context enhancement is not applied. Validation
True/False/None must remain distinct: KR_RRN and KR_FRN can return None for
modern randomized identifiers with no valid legacy checksum. Upstream scores
are detector scores, not probabilities or a real-person/credential conclusion.
Upstream PatternRecognizer catches regex TimeoutError and logs a warning; the
caller must fail that scan/profile rather than silently count it as complete.
The helper attaches a handler to propagate this condition.

Upstream verification (official sources, checked 2026-10-20):
- https://api.github.com/repos/gitleaks/gitleaks/releases/latest -> v8.30.1
- https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1
- https://github.com/gitleaks/gitleaks (feature complete, security patches only)
- https://github.com/gitleaks/gitleaks/blob/v8.30.1/cmd/root.go
- https://github.com/gitleaks/gitleaks/blob/v8.30.1/detect/location.go
- https://api.github.com/repos/gitleaks/gitleaks/security-advisories -> []
  This public query is not a guarantee of no undisclosed vulnerabilities.
- https://pypi.org/pypi/presidio-analyzer/2.2.364/json
- https://github.com/microsoft/presidio/tree/2.2.364 (redirects to data-privacy-stack)
- https://pypi.org/pypi/regex/2026.9.29/json
Gitleaks and Presidio MIT licenses, regex Apache-2.0 AND CNRI-Python license,
and wheel metadata are preserved. Existing Hayabusa AGPL/DRL obligations remain.

Synthetic verification:
  FORSIC_SENSITIVE_IMAGE=<immutable-candidate> python3 -m unittest test_sensitive_install test_forensic_tool_packages -v
This runs no real evidence or model and mounts no case state. The real candidate
smoke disables the network and uses seccomp to kill on socket/connect/send
syscall attempts (none observed), loads all eight selected recognizers, checks
no NLP modules loaded, executes the real CLI and verifies inline allow cannot
suppress the selected synthetic token. It reports observed time and peak RSS;
these are a small synthetic run, not an engine benchmark or field quality claim.
