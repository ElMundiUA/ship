## Сводка треда (после консолидации)

**Задача:** в **zero-target** stuck-issue sweep при ошибках Slack уровня **membership / channel access** (бот не в канале, `not_in_channel`, формулировки вроде «bot is not in this channel») прогон остаётся **успешным** с **warning-level** аудитом (каналы, код/текст ошибки, remediation). Ошибки **не** membership/access — по прежней политике (фатально, где так задумано). Логика stuck-target / retry — не менять.

**Каналы из контекста:** `C0A3FKM3FHD` (support), `C0A4R496C9W` (new-channel) — для доставки нужен бот в этих/настроенных каналах.

**Ссылки из треда:**
- PR https://github.com/ElMundiUA/elmundi/pull/33 (ветка `cursor/developer-agent-implementation-7b37`) — доработка классификации Slack + тесты
- PR https://github.com/ElMundiUA/elmundi/pull/31 — фигурировал в отчётах валидации
- Примеры recovery-коммитов/веток: `82293f5a38` на `cursor/ELM-64-preview-failure-recovery-9e72`, `de3b2679d0`, `eaeb06e792` на `cursor/ELM-64-preview-failure-recovery-532c`

**Тесты:** многократно прогонялся `python -m unittest bin/ops/test_standup.py` (из `.venv`).

**Инфра / превью:** часть прогонов упиралась в **403** preview (`mc-hgca7qjqe2.b-cdn.net` после cleanup) и в **Slack** (бот не в канале) — это отдельно от логики классификации в репо.

**Статус в Linear:** открыт (Todo). Детальная спека — в **описании issue**; предыдущие комментарии удалены как шум агентских повторов (A8, A2, BA, intake, десятки однотипных аудитов).
