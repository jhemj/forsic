# Korean formats, context and counting

Select explicit text encodings from available source metadata; use supported UTF-8, UTF-16 or CP949 handling
only when the runtime advertises it. Record undecodable/skipped ranges. Byte offsets refer to the declared
encoding/input, not character positions; preserve original file/line/field or scalar coordinates.

Compare field headers (for example customer/contact/identifier labels), surrounding record structure, period,
and provenance before adopting candidates as actual personal data. Synthetic fixtures, placeholder identifiers,
logs quoting test values and checksum/format coincidences are alternatives. Presidio recognizer validation is
format validation, not proof of a real person or legal classification.

Keep occurrence count, field count, selected record count, opaque distinct-value groups, file count and people
count separate. Phone normalization may group format variants; tokens remain case-sensitive and email local
parts must not be indiscriminately folded. Multiple fields/rows/files may concern one person or share values.
Scope counts describe selected supported ranges only. Do not extrapolate from samples or file size to the
whole population. Access, copy/archive preparation, external reachability and actual transfer need their own
records linked by file/content/session/time; discovery alone proves none of them.
