# Baseline metrics

Per-build health numbers for the client data, and the known-benign analysis
errors. Read this when logging a new build's metrics or judging whether an
encrypted-file count moved.

## Baseline metrics (1.60.1.69913)

Track these per build; movement is signal.

| Metric | Value |
|---|---|
| Encrypted files | 5035 |
| ESpecs loaded | 6633 |
| DBD definitions | 1342 |
| Relations / label columns | 531 / 12 |
| Enum mappings / definitions | 606 / 354 |

A **drop in encrypted file count** means keys leaked or content unlocked — one
of the highest-value early signals in beta datamining. Log it every build,
together with the `EncryptedUnknownKey` / `EncryptedButNot` split, which
matters as much as the total. A `MATCH` is only a measurement if the file set
moved and the encrypted count did not.

## Known benign errors

These FDIDs throw "Specified argument was out of the range of valid values"
during analysis. Malformed or placeholder entries, non-blocking:

```
5014017, 5014019, 5014022, 5014025, 7018189,
7079572, 7576057, 7576058, 7576059, 7576060
```

Log and continue. Only investigate if the set changes between builds.
