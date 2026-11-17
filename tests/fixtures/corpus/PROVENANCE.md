# Corpus fixtures — provenance

Real field evidence, copied read-only from the review corpus
(`~/FenrisWorkspace/dgx-vitals-review/corpus-sleipnir/dgx-vitals-test/`).

One mechanical change, applied to every file: the site hostname prefix
`nesd-oma01c-` is replaced by `fx-` (in file contents and in paths). No
other byte is changed. summary.csv files keep only the rows named (header
unchanged). The cdw-validation bundle files come from the ferry tarball
`moonlite-validation-bundle.tar.gz` (inner bundle named below); only its
text evidence files are kept (no dump archive, no var-log-nvsm).

Tests copy these into a tempdir before use and stamp summary.csv as the
newest file (as the runner leaves it), so checkout order cannot make a
csv look stale.

`sha256` is of the corpus source file before substitution; `subs` is the
number of prefix substitutions made.

| fixture | source | sha256 (source) | subs |
|---|---|---|---|
| `full-w08-hollow-stress/fx-w08-prod-hv-01/fx-w08-prod-hv-01-FULLLOG.txt` | `dgx-vitals-test/validation-output-full/nesd-oma01c-w08-prod-hv-01/nesd-oma01c-w08-prod-hv-01-FULLLOG.txt` | `23ae50a1a7e571239ca6591503aed003267d44f792124122d01435da1422efc5` | 4 |
| `full-w08-hollow-stress/summary.csv` | `dgx-vitals-test/validation-output-full/summary.csv (rows: fx-w08-prod-hv-01)` | `b3b73d94374e463935108a92c8325c9c5446242946559265c2df9438d0eb23a4` | 1 |
| `wave1-w08-already-running/fx-w08-prod-hv-01/fx-w08-prod-hv-01-FULLLOG.txt` | `dgx-vitals-test/validation-output-wave1/nesd-oma01c-w08-prod-hv-01/nesd-oma01c-w08-prod-hv-01-FULLLOG.txt` | `acc4f06779cbe9eed7418575cf1428f1a5cddec4b4fa2c69a5e193b87d1f564c` | 5 |
| `wave1-w08-already-running/summary.csv` | `dgx-vitals-test/validation-output-wave1/summary.csv (rows: fx-w08-prod-hv-01)` | `785230d59f12b1d02f09f244394cf10ddef1d184c8c4f4a96acf1c5de76d7318` | 1 |
| `wave1-w09-full-pass/fx-w09-prod-hv-01/fx-w09-prod-hv-01-FULLLOG.txt` | `dgx-vitals-test/validation-output-wave1/nesd-oma01c-w09-prod-hv-01/nesd-oma01c-w09-prod-hv-01-FULLLOG.txt` | `f4a0dc14b7b77a485a742c75c315d2c90e8188fb2f273bc57629a365b4f88777` | 5 |
| `wave1-w09-full-pass/summary.csv` | `dgx-vitals-test/validation-output-wave1/summary.csv (rows: fx-w09-prod-hv-01)` | `785230d59f12b1d02f09f244394cf10ddef1d184c8c4f4a96acf1c5de76d7318` | 1 |
| `wave1-x10-ki2/fx-x10-prod-hv-01/fx-x10-prod-hv-01-FULLLOG.txt` | `dgx-vitals-test/validation-output-wave1/nesd-oma01c-x10-prod-hv-01/nesd-oma01c-x10-prod-hv-01-FULLLOG.txt` | `ac33be9a7ec1ff9db78750bf4a41b0f0b7c9107389299f2257699a1099c63d0f` | 4 |
| `wave1-x10-ki2/summary.csv` | `dgx-vitals-test/validation-output-wave1/summary.csv (rows: fx-x10-prod-hv-01)` | `785230d59f12b1d02f09f244394cf10ddef1d184c8c4f4a96acf1c5de76d7318` | 1 |
| `xwave2-no-summary/fx-x01-prod-hv-02/fx-x01-prod-hv-02-FULLLOG.txt` | `dgx-vitals-test/validation-output-x-wave2/nesd-oma01c-x01-prod-hv-02/nesd-oma01c-x01-prod-hv-02-FULLLOG.txt` | `b3fe37bd716e10c333881dda84e7ffde9cafee8586189b5fafe5fadcbb88054c` | 2 |
| `xwave2-no-summary/fx-x01-prod-hv-04/fx-x01-prod-hv-04-FULLLOG.txt` | `dgx-vitals-test/validation-output-x-wave2/nesd-oma01c-x01-prod-hv-04/nesd-oma01c-x01-prod-hv-04-FULLLOG.txt` | `6c34e00c6126da9461895ae2532d51413c0b1e9309cef4ef564519979db5f5b6` | 2 |
| `xwave2-summary-rows/summary.csv` | `dgx-vitals-test/validation-output-x-wave2/summary.csv (rows: fx-x01-prod-hv-02, fx-x01-prod-hv-04)` | `a9409641f0e949d7b307ac299886e42f5e5e986089d4b51c52c86ba2baf34e6d` | 2 |
| `y10-truncated/fx-y10-prod-hv-02/fx-y10-prod-hv-02-FULLLOG.txt` | `dgx-vitals-test/y10hv02-run1/nesd-oma01c-y10-prod-hv-02-FULLLOG.txt` | `8f39c1fef4da6df9179f78af8e8c4bff5c325e9fe1e039b3590e62676fc33e8b` | 1 |
| `bundle-w09/fx-w09-prod-hv-01/cdw-validation-fx-w09-prod-hv-01-20261111-174304/summary.txt` | `moonlite-validation-bundle.tar.gz → bundles/cdw-validation-nesd-oma01c-w09-prod-hv-01-20261111-174304.tar.gz → summary.txt` | `12959706835715526f73c353cc962a9bb938a9305046f6de610038366da3682f` | 1 |
| `bundle-w09/fx-w09-prod-hv-01/cdw-validation-fx-w09-prod-hv-01-20261111-174304/03-nvsm-versions.log` | `moonlite-validation-bundle.tar.gz → bundles/cdw-validation-nesd-oma01c-w09-prod-hv-01-20261111-174304.tar.gz → 03-nvsm-versions.log` | `b9764b03ce16b2d81b8020618b77c222cc4a5db555feb4c5def6c8dcef080ede` | 0 |
| `bundle-w09/fx-w09-prod-hv-01/cdw-validation-fx-w09-prod-hv-01-20261111-174304/04-nvsm-health.log` | `moonlite-validation-bundle.tar.gz → bundles/cdw-validation-nesd-oma01c-w09-prod-hv-01-20261111-174304.tar.gz → 04-nvsm-health.log` | `a62a8620adc336228f1a90a6875800835479f0bc7470ca4b31d38adb876e5fec` | 0 |
| `bundle-w09/fx-w09-prod-hv-01/cdw-validation-fx-w09-prod-hv-01-20261111-174304/system-info.txt` | `moonlite-validation-bundle.tar.gz → bundles/cdw-validation-nesd-oma01c-w09-prod-hv-01-20261111-174304.tar.gz → system-info.txt` | `65cc921d50d5ade6c5962273a5fe95b040eaf3ff241ebb2a6b8b6010781910b2` | 2 |
