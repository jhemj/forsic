---
name: evidence-intake
description: Check the investigation scope, image format, segments, and integrity of new evidence.
---
# Evidence intake
Check questions and scope through forsic_reporting(action=case) and reuse existing intake results. Locate evidence
with forsic_list; for E01, check format and volumes with image_info and image_files(action=volumes).
hash is the current SHA-256; it does not establish acquisition integrity until compared with the acquisition-time
verification value. forsic_verify action=start launches ewfverify once in the background and immediately returns
status. The same input reuses an ongoing job or completed result. Investigate other questions, then check completion
with action=status and the same path. Do not repeatedly poll status at short intervals.
In-progress, failed, or unknown interruption status is not completion. The timeout budget is an operator setting;
do not blindly restart the same failure as a new job. Explain needed budget changes to the user.
Even SUCCESS establishes only the scope checked when no acquisition-time comparison hash exists.
Distinguish missing segments, encryption, unsupported formats, and verification failure. Do not describe unreadable
areas as absent.
