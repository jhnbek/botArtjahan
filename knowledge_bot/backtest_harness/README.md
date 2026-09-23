# Backtest Harness

This is a safety-gated, fixture-first scaffold for a future read-only harness.
It currently exposes synthetic validators and contract/preflight helpers; real
market-history execution and trading remain blocked.

Allowed now:

- CLI help and safety status.
- Scaffold-only run manifest generation.
- Prohibited-capability scanning of local scaffold code.
- Placeholder component contracts.
- Fixture-only validation over tiny synthetic local historical-data fixtures.
- Synthetic-fixture-only manifest inventory metadata validation.
- Fixture-first explicit-manifest metadata validation over synthetic manifest documents only.
- Fixture-mode manifest metadata validation execution path over synthetic manifest documents only.
- One-explicit-manifest metadata validation for a user-provided JSON manifest document only.
- Checksum and size validation over tiny synthetic public-seed archive fixtures only.
- Synthetic SCN-002 observation validation with causality and field-boundary checks.

Blocked now:

- Historical data loading.
- Detector execution over market history.
- Outcome labels and PnL.
- Split-manifest generation.
- Offline detector observation.
- Runtime signals.
- Paper trading.
- Live trading.
- Broker or exchange integrations.

`validate-fixtures` is intentionally narrow: it checks manifest fields, local file hashes, OHLC bounds, timestamp monotonicity, forbidden future/outcome fields, and no-future-data boundaries for synthetic JSONL fixtures only. It is not a real historical data adapter and does not permit market-history ingestion.

`validate-manifest-inventory-fixtures` is also intentionally narrow: it checks only synthetic manifest metadata fixtures for required fields, file-entry metadata, crypto spot 24/7 UTC profile consistency, path safety, expected file counts, and forbidden outcome/PnL/order/fill fields. It does not scan directories, hash listed files, open listed market files, parse rows, load real historical data, build splits, run detectors, compute PnL, or enable execution.

`validate-manifest-metadata-fixtures` is narrower still: it tests the future explicit-manifest metadata validator against synthetic manifest documents and unsafe synthetic inputs. It may open only fixture manifest documents; it does not run against real manifest documents and does not open, hash, size-probe, extract, or parse files listed by manifests.

`validate-manifest-metadata-execution-fixtures` exercises the reviewed execution path in fixture mode only. It reuses synthetic manifest metadata documents, may open only fixture manifest documents, and keeps real manifest documents, manifest inventory execution, source directory scanning, listed-file open/hash/size-probe access, market-row parsing, real historical loading, split generation, detector execution, PnL, backtest, and execution blocked.

`validate-real-manifest-metadata --manifest <path>` validates exactly one explicit JSON manifest document. It may open that manifest document and validates manifest/file-entry metadata only; it still does not scan directories, open/hash/size-probe files listed by the manifest, parse market rows, load historical data, build splits, run detectors, compute PnL, run backtests, or enable execution.

`validate-public-seed-checksum-fixtures` checks checksum parsing, expected hashes, and byte sizes against tiny synthetic archive fixtures only. It does not use the network, access real seed archives, extract archives, parse market rows, load historical data, or enable execution.

`validate-scn002-fixtures` checks synthetic SCN-002 observations for detector-chain shape, source references, decision-time boundaries, and forbidden outcome fields. It does not load market history, execute detectors, label outcomes, compute PnL, or enable trading.
