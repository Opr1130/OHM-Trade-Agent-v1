# v1.4.4 source pin

## Authority

| Field | Value |
| --- | --- |
| Title | OPIP Profit Intelligence Platform Architecture |
| Version | 1.4.4 |
| Subtitle | Precision Agent, Continuous Protection and Experience Intelligence |
| Date | 4 October 2026 |
| Marking | Focused amendment to v1.4.3 • 4 October 2026 |
| Owner path | `C:\Users\ohmpr\Documents\OHM AI\OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` |
| Repository copy | `docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` |
| SHA256 | `9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768` |
| Size | 70601 bytes |
| Extraction | `docs/architecture/v1.4.4/ARCHITECTURE.md` |

The owner file was not edited, moved, renamed, or deleted. The repository DOCX is a byte copy: its SHA256 was recomputed inside the repository and equals the owner file's SHA256 above. `ARCHITECTURE.md` is a paragraph extraction of that DOCX. The DOCX controls if the extraction and the DOCX disagree.

## Identity checks

Body text states:

- `Version 1.4.4`
- `Precision Agent, Continuous Protection and Experience Intelligence`
- `Focused amendment to v1.4.3 • 4 October 2026`

`docProps/core.xml` titles the package `OPIP Profit Intelligence Architecture v1.4.4`, matching the body version. Unlike the v1.4.3 package, no stale document property contradicts the body.

## Duplicate census

Exact-name search of `Documents`, `Desktop` and `Downloads` found one file:

`C:\Users\ohmpr\Documents\OHM AI\OPIP_Profit_Intelligence_Architecture_v1_4_4.docx`

No second v1.4.4 copy was selected.

## Position in the pinned set

- v1.4.4 is a focused amendment to v1.4.3. Its section 1 preserves v1.4.3 history, and its Appendix C is a source register and adoption record naming the v1.4.3 authority copy, the repository pin, and the adoption fields that still have to be completed.
- v1.5.0 supersedes v1.4.4 and retains it as the amendment record (`docs/architecture/v1.5.0/SOURCE.md`).
- The baselined v1.4.3 package, its `SOURCE.md` and its DOCX bytes are unchanged; v1.4.4 was added beside them rather than replacing them.

## Extraction method (reproducible)

`ARCHITECTURE.md` was produced from the DOCX with the Python standard library only (`zipfile` + `xml.etree` over `word/document.xml`), in document order:

- one paragraph per line, leading indentation preserved and trailing whitespace trimmed;
- a source table's cells become sequential lines in document order, and empty cells are omitted;
- a paragraph containing a Word page break becomes a blank line, while a paragraph with no run content at all is omitted;
- a line break (`w:br`) inside a paragraph is concatenated without a separator, so a title split across two lines reads as one line. This is the rule the committed `docs/architecture/v1.4.3/ARCHITECTURE.md` already follows: the same method reproduces that committed file byte-for-byte;
- no trailing newline, matching the v1.4.3 artefact.

The extraction is a reading aid. It is not the authority and it does not redesign the architecture.

## What v1.4.4 covers

Its own section titles are: 1 Authority and preserved history; 2 Precision profit objective; 3 Continuous sensing and account guardian; 4 Confirmation and contradiction; 5 Selective AI Committee analysis; 6 Experience and measurable learning; 7 Causal confidence and attribution; 8 F10 decision cockpit requirements; 9 Signal quality and Paper v2 usefulness; 10 Traceability scorecard semantics; 11 Capability completion snapshot; 12–15 traceability for observation and qualification, economics and paper execution, learning and operator trust, and cross-cutting concerns; 16 Delivery sequence and closure criteria (stages: stabilize current release, adopt the amendment, recover signal quality, build operator visibility, consolidate experience, decide paper cutover); Appendix A precision agent acceptance contract; Appendix B signal quality measurement contract; Appendix C source register and adoption record.

## What this pin does not do

This document does not certify deployed capabilities, approve a release, grant funded or live authority, or activate any capability. Its Appendix C adoption record lists items that remain open — accountable business and technical owners, the exact amendments adopted, and unresolved numeric acceptance decisions. This pin records source identity, hash, size and repository path only; the ownership and numeric questions stay open and are not resolved here.
