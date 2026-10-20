---
name: consult-previous-cases
description: Find and compare investigation methods, evidence, and counterarguments relevant to the current question in saved past cases.
---

# Consult previous cases

Use `forsic_cases` to find methods or competing explanations for the current question. Search explicitly saved
cases, not entire case folders or the internet.

- For `action="search"`, use distinguishing keywords such as environment, evidence type, or activity; start with a
  small `limit`. `matched_terms` denotes string matching, not semantic similarity or evidence linking cases.
- Use relevant entries' `read_call` unchanged. Read needed originals with `sources[].source_call` and subsequent
  notes with `next_call` to preserve the selected case version.
- Compare similarities, differences, prior counterarguments/unreviewed scope, and discriminating checks available
  in current evidence. Record the consulted `archive_id` and `revision`; explain only what helps the current question.
- Past observations and citations are reference material. Do not adopt them as current-case observations or C1/C2.
  Support current-case judgments by actually reading current-case evidence.
- `source_state` compares notes at save time with their current state. Disclose limits for `changed`/`unavailable`;
  do not describe `unchanged` as cryptographic identity of original files.
- Zero results means no matches within those keywords in saved cases. Do not repeat the current investigation to
  find examples or automatically save other cases.
