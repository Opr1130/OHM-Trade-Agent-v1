# v1.5.0 source pin

## Authority

| Field | Value |
| --- | --- |
| Title | O’Pip Profit Intelligence Platform Architecture |
| Version | 1.5.0 |
| Subtitle | Continuous Multi-Horizon Capital Intelligence |
| Date | 9 October 2026 |
| Marking | Architecture revision • 9 October 2026 • R5 delivery direction |
| Owner path | `C:\Users\ohmpr\Documents\OHM AI\Oct 2026\OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` |
| Repository copy | `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` |
| SHA256 | `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2` |
| Size | 63023 bytes |
| Extraction | `docs/architecture/v1.5.0/ARCHITECTURE.md` |
| Owner feature-priority tracker (markdown) | `docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md` — SHA256 `22f66a737f7a49fec8c2a7652d0324e3f942d8a488746d5a1e581c1921d98b68`, 4163 bytes |
| Owner feature-priority tracker (CSV) | `docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.csv` — SHA256 `2d7ca0ddfc8164a924333e4ff02fddfca82f4109312089631baa4b7bc9da7354`, 6205 bytes |

The owner files were not edited, moved, renamed, or deleted. The two tracker text files carry the owner CRLF line endings, so `docs/architecture/v1.5.0/.gitattributes` marks them non-text: git then stores those bytes verbatim, no add or checkout normalises them, and their SHA256 stays equal to the value recorded above. Every repository copy is a byte copy: each SHA256 was recomputed inside the repository and equals the owner file's SHA256 above. `ARCHITECTURE.md` is a paragraph extraction of the DOCX. The DOCX controls if the extraction and the DOCX disagree.

## Identity checks

Body text states:

- `Version 1.5.0`
- `Continuous Multi-Horizon Capital Intelligence`
- `Architecture revision • 9 October 2026 • R5 delivery direction`

`docProps/core.xml` titles the package `O Pip Profit Intelligence Platform Architecture v1.5.0`, matching the body version.

Section 19 records its own sources: S1 the v1.4.3 authority copy (`ab494a19…`, 84,643 bytes), S2 this v1.4.4 amendment copy (`9eb48784…`, 70,601 bytes), S3 `AGENTS.md` and `docs/architecture/CURRENT_ARCHITECTURE_STATUS.md` at repository commit `8b3cc2712432ca21007be4db0667301d48b89d97`, S4 `OPIP_RECOVERY_ROADMAP.md` at that commit, S5 `OPIP_CONFORMANCE_LEDGER.md` at that commit, S6 a discussion transcript. The hashes recorded in S1 and S2 match the values pinned here and in `docs/architecture/v1.4.4/SOURCE.md`.

Section 19 S3 records that "the status file still pins v1.4.3" at `8b3cc271`. The source-adoption increment `ATDD-R5-0-architecture-source-adoption` exists to answer that observation; the reconciled truth documents now name the adopted sources and this adoption baseline.

## Duplicate census

Exact-name search of `Documents`, `Desktop` and `Downloads` found one copy of each file, in the owner folder:

- `C:\Users\ohmpr\Documents\OHM AI\Oct 2026\OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`
- `C:\Users\ohmpr\Documents\OHM AI\Oct 2026\OPIP_Feature_Priorities_v1_5_0.md`
- `C:\Users\ohmpr\Documents\OHM AI\Oct 2026\OPIP_Feature_Priorities_v1_5_0.csv`

No second copy of any of these was selected.

## Position in the pinned set

- v1.5.0 supersedes the v1.4.4 amendment of 4 October 2026, which amended the v1.4.3 body of 22 September 2026. All three packages are retained in the repository.
- Section 1 preserves the inherited contracts, and section 14 keeps the numbering continuous: R5 now means Continuous Multi-Horizon Capital Intelligence, and the earlier repository R5 milestone (outcome consolidation and the cockpit) is preserved in tracking as `R5-LEGACY-OUTCOMES-COCKPIT`. R6 Committee shadow proof and R7 governed retirement remain separate retained obligations.

## Extraction method (reproducible)

`ARCHITECTURE.md` was produced from the DOCX with the Python standard library only (`zipfile` + `xml.etree` over `word/document.xml`), in document order, using the same rules as `docs/architecture/v1.4.4/ARCHITECTURE.md` and the committed `docs/architecture/v1.4.3/ARCHITECTURE.md`: one paragraph per line; source-table cells as sequential lines; empty cells omitted; page-break paragraphs as blank lines; paragraphs with no run content omitted; a line break inside a paragraph concatenated without a separator; no trailing newline. The method reproduces the committed v1.4.3 extraction byte-for-byte.

The extraction is a reading aid. It is not the authority and it does not redesign the architecture.

## The feature-priority tracker is planning input, not authority

`OPIP_Feature_Priorities_v1_5_0.md` and `.csv` are the owner's ordered priorities. They state that owners are `UNASSIGNED`, that non-architecture dimensions are `NOT_ASSESSED`, that categories are not completion claims, and that the list grants no implementation, release, `TARGET_PAPER` or funded authority. They are copied here as source evidence only. They change no contract, no status value and no authority.

## What this pin does not do

This document does not certify deployed capabilities, approve a release, grant funded or live authority, or activate any capability. It does not approve the v1.5.0 delivery sequence. In particular it does not approve: R5-0's owner assignment, the record/horizon/accounting/performance-budget freezes, R5-A Market Eye, R5-B Horizons, R5-C Capital, R5-D Portfolio and rotation, R5-E Autonomous paper, R5-F Learning, any F9/F10 change, `TARGET_PAPER`, cutover, or any production configuration change.
