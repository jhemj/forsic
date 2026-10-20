# Local extension format (version 1)

The hub imports ZIP files with `manifest.json` at the root. It preserves each
ID/version in the served Hermes profile's `forsic/extensions/packs/ID/VERSION`.
Import validates files without importing Python modules, running hooks, installing
dependencies, preprocessing skills, or making network requests. There is no central
catalog. `examples/extensions/skill` and `examples/extensions/tool` contain editable,
synthetic examples. ZIP each directory's contents, with no enclosing directory.

## Manifest

`format_version` and `compatibility.extension_api` are both `1`. Required fields:

- `id`: namespaced lowercase ID such as `local.text-tools`.
- `version`: immutable version or explicit local revision, up to 64 safe characters.
- `display_name`, `description`, and `kind` (`skill`, `tool`, or `mixed`).
- `source`: object with `type`, such as `local-authored` or `repository`. When known,
  record repository, commit, release, subdirectory and modifications. Include the
  original LICENSE/NOTICE as declared payload files. Do not invent missing metadata.
- `compatibility`: extension API plus supported `platforms` (`linux/amd64` etc.).
- `files`: every payload relative path mapped to its SHA-256. The manifest itself
  is excluded. Its canonical JSON hash identifies the complete declared content.
- `tools` / `skills`: provided descriptors. `dependencies` lists requirements for
  the environment manager; importing never installs them on the host.

Dependencies use exact typed descriptors: `{"method":"apt","name":"jq","version":"1.7-1"}`
or `{"method":"python","name":"example","version":"2.0","sha256":["<wheel hash>"]}`.
The environment manager translates these into the existing preparation plan. Unsupported
methods, unpinned versions, duplicate names and missing Python hashes are rejected.
Native menus and dispatch require the selected environment's measured installed versions
to match. Packages installed in the isolated Python environment require an executable
under `/opt/forsic-deps/bin/`; using the base `python` does not silently inherit them.

A tool declares `name`, `description`, a closed object JSON `parameters` schema,
`argv`, `timeout_seconds` (1–900), `max_output_bytes` (1–1048576), and optional
`output: {"format":"text"}` or `json`. The argv list starts with a literal
executable. Other elements are literal strings or `{"argument":"schema_field"}`
for scalar values. Values are validated against the exact version's schema and
encoded with `shlex.join` at the existing shell boundary. There are no arbitrary
mount, Docker option, host import, network or privilege fields. Container adapter
files are addressed under `/extension`; selected originals stay under the existing
read-only `/evidence` mount and derived artifacts stay under `/work`.

Tool `fixtures` may declare `path`, matching `sha256`, `arguments`, and structural
`expected` output. A fixture's presence is not a successful process execution.
Structure verification, environment availability and actual functional verification
are distinct states. Existing evidence execution records retain actual process
status; neither a pack's assertions nor a generated expected result certify it.

A skill declares `name`, `path` ending in `/SKILL.md`, and `requires_tools`.
SKILL.md preserves native frontmatter with matching `name` and `description`.
References are declared payload Markdown files under that skill's `references/`.
Native readers load only the selected body/reference with `preprocess=False`.
No script executes when listing or reading a skill.

## Validation and preservation

Limits are 512 ZIP entries, 32 MiB compressed/expanded total, 8 MiB per file, and
an expansion ratio bound. Absolute paths, traversal, backslashes, drive paths,
links, special files, encryption, duplicate/casefold/Unicode normalized names,
missing/extra payloads, mismatching hashes and builtin name overrides are rejected.
Identical ID/version/content imports are idempotent. Different content under the
same ID/version is rejected. Export selects only declared payloads and the manifest,
never the profile, investigation logs, builder workspace or credentials. Reimport
preserves content identity even if ZIP timestamps/order differ.

Profile defaults are a CAS revision with an operation ID. A selection has shape:

```json
{"packs":{"local.text-tools":{"version":"1.0.0","sha256":"<manifest hash>","enabled":true,"tools":{"count-lines":true},"skills":{}}}}
```

A case's environment revision owns its exact extension selection. Profile defaults
initialize future pins; they do not rewrite pinned cases. Case updates use the
environment manager's pending/apply boundary, preserving active native turns.
Rollback reselects an installed immutable version. Internal native registration
names include a pack content hash so case A's v1 schema/adapter can coexist with
case B's v2. Menu callbacks and native dispatch resolve the owning session's case;
dispatch verifies the pinned manifest and all payload hashes again.

Pack, individual tool and individual skill switches are separate. A disabled
required tool makes its skill unavailable; it is never silently reenabled.
Tool OFF blocks native exposure and adapter dispatch. An enabled general terminal
can still run binaries installed in the environment; switches do not remove them
or promise OS isolation. Deletion rejects referenced versions; the management API
must supply all case and pending revision references, not just the open session.

Environment preparation resolves actual dependencies and platforms. A lightweight
pack alone does not promise complete offline installation; an offline environment
bundle must include preserved images, helpers and the required extension packs.
