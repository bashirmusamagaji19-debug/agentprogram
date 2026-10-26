# model-value-v3 revision log

This directory is a new frozen version. It does not modify earlier frozen directories.

- Supersedes dataset: `model-value-v2` version `2.0.0`
- Superseded frozen manifest self-SHA256: `d737c239d3f69e6ca82ba2e564b66c99e75ddd4ffe7cf736edf68c4e401bcd13`
- Correction: align g13/g14 task gold with accepted source pages, inject the empty-model response fault explicitly, accept recruitment-data technical titles, and stratify all boundary groups into the formal test split

## Prior-version known errors

- v2 labeled c3-g13/g14 source pages as accepted while assigning a blanket refusal task gold
- v2 had no controlled empty-model-response injection
- v2 rejected the technical recruitment-data title by substring alone
- v2 test was dominated by clear single-candidate pages and omitted most boundary behaviors

Earlier versions remain readable for audit and reproduction. Any future label correction must
produce another new version; frozen label bytes are never edited in place.
