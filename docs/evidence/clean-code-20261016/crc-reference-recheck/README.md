# Explicit independent CRC reference recheck

After the first full second-pass run was preserved, the exact existing documented
team reference was retrieved read-only from its fixed upstream commit. Its 5356
bytes match the previously recorded SHA-256 in full, before and after testing.
The external file is not copied into this repository and no firmware is selected,
installed, patched or substituted. See reference-manifest.json for the exact URL,
commit, hash, explicit input and command.

At clean integration source `ddaa286`, the registered polar-link check passes
all **81 assertions**, including both CRC comparisons. The original first full
run's 79 assertions plus missing-reference INCOMPLETE remain preserved unchanged.
That missing input already existed in wave13; it was not newly lost during the
environment refresh. This targeted recheck does not retroactively make the
first full run pass. A subsequent full aggregate must record this explicit
external input separately from its source fingerprint.
