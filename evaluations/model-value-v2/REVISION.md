# model-value-v2 revision log

This directory is a new frozen version. It does not modify `evaluations/model-value-v1`.

- Supersedes dataset: `model-value-v1` version `1.0.0`
- Superseded frozen manifest self-SHA256: `e337bc818ded52d5d9269b6a851006baf6269cbdfc3f19719c026fc1b0672464`
- Correction: conditions and accepted labels now require literal query and AI-role source evidence

## v1 known errors

- v1 assigned blanket `AI 实习` or `机器学习 实习` keywords across groups
- some v1 accepted pages, including c3-g01-v1, had no AI term or AI role evidence
- v1 is retired for formal inference and remains frozen for audit only

v1 remains readable for audit and reproduction. It is retired for formal inference. Any future label
correction must produce another new version; frozen label bytes are never edited in place.
