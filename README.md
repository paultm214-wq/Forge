# Forge reviewed code package

[Download the revised code package](downloads/forge-reviewed.tar.gz?raw=true)

The archive contains the revised source, review report, tests, and validation evidence.

Validation: 641 tests passed; 7 optional checks skipped. Live GPU and LAN integrations were not tested. See `Forge/REVIEW.md` inside the archive for findings and migration notes.

The supplied camera credential was removed from the revised files. Rotate it if it is live or reused.

Verify the download with `sha256sum -c forge-reviewed.tar.gz.sha256` after downloading both files from `downloads/`.
