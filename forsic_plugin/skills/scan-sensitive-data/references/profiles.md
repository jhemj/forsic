# Profiles and execution meaning

`secrets` uses the pinned local Gitleaks CLI/rule snapshot. It reports credential-shaped candidates, including
keys/passwords/private keys supported by those rules. Runtime capability and rule receipts establish what was
actually available. No online verification, rule download, automatic engine replacement or TruffleHog default.

`pii_kr` uses selected local Presidio recognizers and bounded local format/context helpers. Korean identifier,
phone, email and card candidates depend on actual advertised recognizers. Pattern scores are detector evidence,
not probabilities of real people or an incident. Names and free-prose addresses are not complete Korean NER.

Use the existing shell capabilities response and scan's engine/rule/coverage fields. Missing engines, exceptions,
partial inputs and unavailable parsers must be reported accurately. Files such as databases, archives, Office/PDF
or encrypted material require supported extraction with retained provenance; raw binary matching is not an
adequate substitute. Do not run scanner commands through generic run to bypass safe output handling.
