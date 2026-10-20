---
name: personal-data-scope
description: Investigate personal-data holdings, access, and external transfer as separate questions in selected evidence, reporting samples and unreviewed scope.
---
# Establish personal-data scope

Apply when personal-data questions arise or findings change impact scope. Do not start whole-disk personal-data
collection in every case. Check existing notes and results before repeating collection.

Separate possession → access → exfiltration preparation → transfer into distinct propositions. Filenames alone do
not establish possession; possession alone does not establish leakage. In relevant documents, exported CSVs, or
logs, inspect schemas, record types, periods, and minimal samples. If an original database, archive, or document
parser is unavailable, do not pretend supported text reading replaces it; record the needed format and next check.

- Show only the minimum sample fields needed and mask personal values. Names or number patterns alone do not
  establish personal-data types. Distinguish example/test data in logs from actual records.
- Sample counts, total file size, and unique-person counts differ. Without counting full scope, do not estimate
  totals or volumes. Record files, periods, and regions read and unread.
- For access, compare audit/authentication/application query records with targets, subjects, and times. Account
  ownership or file atime alone does not establish who read content.
- For exfiltration, check file/time/session links between copying/archiving traces and transfer records. Originals
  are needed to distinguish legitimate backups or business exports. Continue through data-movement as appropriate.

Save the current answer through forsic_question with C2/C1 references selected from actual reads. Investigate
important gaps through direct reads/searches; use missions only when advance discrimination criteria need
preservation. In the report body, state confirmed information types/scope and current answers and limits on
access/transfer; put original locations and check scope in the same document's technical appendix. Do not add
sections substituting for reporting, notification, legal judgments, or management decisions.

When selected holdings need content or bounded counts, use scan-sensitive-data. Keep candidate format, actual-data context, value groups and person counts distinct. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
