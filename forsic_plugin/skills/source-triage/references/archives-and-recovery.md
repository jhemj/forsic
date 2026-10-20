# Allocated archives and recovery limits

The image/archive readers here read allocated files. If deleted-file recovery or signature carving matters to the
question, check analysis-terminal support in `references/deleted-recovery.md`. Absence from allocated-file listings
does not establish impossibility of recovery or a need for additional authorization.

List allocated TAR/TAR.GZ members through forsic_image_files(action="archive_list", path=이미지, file_path=내부경로).
Copy the listing's source_ref to listing_ref, select a global member_index, and read through forsic_archive_read.
Do not re-enter paths for reads; continue through next_call. Listing references are not content evidence. Select
C2 from actual reads.

Member names are archive metadata, not proof of existence, execution, or maliciousness on the original host.
Distinguish duplicate names through member_index; respect next_offset, scan_budget, and complete_member scope.
Identity is metadata_checked; content replacement preserving metadata is not verified. Do not claim to have read
unsupported formats, links, or special files. Do not automatically unpack nested archives or execute members.

For static inspection of ZIP/OOXML and similar file structures, read forsic_skill(name="inspect-documents").
This is derived examination of allocated files, not deleted-file recovery.
