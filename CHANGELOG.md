# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-04-21

### Added
- Initial release based on chilbolton-pressure-utils v1.0.0
- Processing of RAL Drop Counting Gauge rainfall data from Campbell Scientific
  CR1000X datalogger files (`rg001dc_ch_Tot` column, values in mm)
- Legacy Format5 binary file support (`rg001dc_ch` channel — verify channel name
  against the site channel database)
- STFC variant for data collected under STFC affiliation
- Bad data indices management via `extract-raingauge-bad-data-indices` and
  `apply-raingauge-bad-data-indices`
- Batch year and month processing via `process-raingauge-year[-f5|-stfc]`
  and `process-raingauge-month[-f5|-stfc]`
- Quicklook plot generation via `make-raingauge-quicklooks`
- CF-compliant NetCDF output using NCAS AMoF NetCDF Template
  (`ncas-rain-gauge-1`, product `precipitation`)
