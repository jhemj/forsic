---
name: inspect-documents
description: Statically inspect case Office/CSV documents and distinguish extraction limits for formats requiring specialist parsers, including PDF. Use existing reporting for report generation.
---
# Inspect document and table evidence

First select actual file formats/sizes and question-relevant portions. Originals are read-only. Embedded commands, links, macros, and external data connections are evidence; do not open, execute, refresh, or connect to them. For image-contained files, follow forsic_skill(name="source-triage", file_path="references/image-access.md"): export only needed files to /work or use the common reader. Preserve copy provenance, byte ranges, and hashes.

Python's standard library in forsic_shell can inspect CSV and ZIP/OOXML members/XML. Read forsic_skill(name="inspect-documents", file_path="references/formats.md") only as needed. Dockerfile.tools declares dissect.target/xfs, without guaranteeing PDF/OCR/Office parsers. Verify actual support through runtime modules/help; record unsupported if absent. Do not install or use external conversion.

For derived inspection, record original location, selected members/sheets/rows/cells, tools/conversions, omissions, and failures. Exact C1/C2 shell-output citations remain derived_command_output, not direct source observation. Do not generalize failed text extraction, partial sheets, or sample rows to absence across the document. Present only personal-data fields needed for the question; follow personal-data-scope for retention/access/transmission decisions.

Connect findings to the current answer through answer-question. Create or revise HTML/Word investigation reports with forsic-report-driven and forsic_reporting(action="render"). Arbitrary separate document generation, template replacement, and evidence overwriting are outside this skill.
