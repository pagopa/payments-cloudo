# Changelog

## [0.31.0](https://github.com/pagopa/payments-cloudo/compare/orchestrator-v0.30.2...orchestrator-v0.31.0) (2026-11-11)


### 🚀 Features

* [PAYMCLOUD-1517] run best-effort Azure login before every runbook and report auth status ([#132](https://github.com/pagopa/payments-cloudo/issues/132)) ([fce18c1](https://github.com/pagopa/payments-cloudo/commit/fce18c186dfd56d08b5740a5f643dd7cc18b3aac))
* [PAYMCLOUD-1522] add schedule severity and read-only view for Terraform-managed schedules ([#138](https://github.com/pagopa/payments-cloudo/issues/138)) ([f56640c](https://github.com/pagopa/payments-cloudo/commit/f56640ca1067bcc068978f0a5edb0f85365d4cb1))


### 🐛 Fixes

* [PAYMCLOUD-1483] prevent duplicate schedule dispatch across scheduler replicas ([#129](https://github.com/pagopa/payments-cloudo/issues/129)) ([9fc974c](https://github.com/pagopa/payments-cloudo/commit/9fc974cd243d006f1c73eaeb8f2a77b389fe136f))
* [PAYMCLOUD-1515] use deterministic RowKey for status update log entries ([#131](https://github.com/pagopa/payments-cloudo/issues/131)) ([e47831f](https://github.com/pagopa/payments-cloudo/commit/e47831f6ca7480ef1fd38e8f5a4f102fe41dde2a))

## [0.30.2](https://github.com/pagopa/payments-cloudo/compare/orchestrator-v0.30.1...orchestrator-v0.30.2) (2026-11-05)


### 🐛 Fixes

* [PAYMCLOUD-1414] approval process to run checks before signature verification ([#118](https://github.com/pagopa/payments-cloudo/issues/118)) ([16c9d7a](https://github.com/pagopa/payments-cloudo/commit/16c9d7aa2114afe22555dfd53526277279d5d8a6))
* [PAYMCLOUD-1415] Improve smart routing sender logic for delivery outcomes ([#120](https://github.com/pagopa/payments-cloudo/issues/120)) ([8663128](https://github.com/pagopa/payments-cloudo/commit/8663128d7227634014c774740b963efc5e6bafca))
* [PAYMCLOUD-1416] Update resource group and namespace keys in approval logic ([#121](https://github.com/pagopa/payments-cloudo/issues/121)) ([adad5b0](https://github.com/pagopa/payments-cloudo/commit/adad5b0c181f5247ca235ad3aa5ca1f869f323d1))
* [PAYMCLOUD-1417] Implement confirmation modal for empty routing rules ([#122](https://github.com/pagopa/payments-cloudo/issues/122)) ([b264c65](https://github.com/pagopa/payments-cloudo/commit/b264c65d2708a76ca4b97953924ae8eecef2d255))
* [PAYMCLOUD-1482] pass aks_login_required flag in dev test run payload ([#127](https://github.com/pagopa/payments-cloudo/issues/127)) ([d13a09f](https://github.com/pagopa/payments-cloudo/commit/d13a09ff59b0c66db4dd8791109c771536b947e6))

## [0.30.1](https://github.com/pagopa/payments-cloudo/compare/orchestrator-v0.30.0...orchestrator-v0.30.1) (2026-10-23)


### 🧰 Refactoring

* [PAYMCLOUD-1412] Refactor cloudo apps in modules for clarity and maintainability ([#112](https://github.com/pagopa/payments-cloudo/issues/112)) ([c9dd115](https://github.com/pagopa/payments-cloudo/commit/c9dd1154b26bc98dc07b5fa5253e83866bbd3aa3))
