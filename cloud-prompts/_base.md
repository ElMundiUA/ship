## Global rules (Cursor Cloud Agent — GitHub SDLC)

- **Очередь SDLC:** GitHub pick-скрипты берут только тикеты в **Todo** и в проекте **ElMundi pre-release** (или `LINEAR_SDLC_PROJECT_ID`). **Backlog** для ручной сортировки — автоматика его не подхватывает, пока ты не перенесёшь карточку в Todo.
- **Linear:** Единый канал с людьми — **комментарии к тикету** в Linear. Не спамь: один содержательный комментарий за проход, помечай конец строкой `[GitHub SDLC:{{ROLE}}]`.
- **IDEMPOTENCY:** Перед изменениями перечитай тикет (и последние комментарии). Если работа для этой роли уже сделана (нужные labels/description/статус), **выйди без изменений и без комментария**.
- **Расписание GitHub:** Один и тот же тикет может снова попасть в pick через 2h. Если последний комментарий с `[GitHub SDLC:{{ROLE}}]` уже отражает актуальное состояние и новых входных нет — **не дублируй** обновления и комментарии.
- Не мержить PR. Не переводить в Done без явного апрува человека.
- **LINEAR_API_KEY:** Должен быть доступен агенту (Cursor Cloud → Secrets / env для репозитория). Обновляй Linear через API или официальный клиент. Если ключа нет — один комментарий `[LINEAR-DRAFT]` с JSON/текстом того, что нужно применить вручную.
- **Скили:** Ниже контекст из `.cursor/skills` — следуй им для Bunny, деплоя, self-heal и т.д., если задача затрагивает эти области.
- **Один тикет — один открытый PR от SDLC:** ветка **`fix/ELM-XX-auto`** (см. `cloud-agent-launch.mjs`). Не создавай параллельно **`feature/ELM-XX-auto`** для того же тикета — это дубликаты; если уже есть два PR, не мержи оба: оставь актуальный, второй закрой.
- **Pre-release / dev→prod:** справочник `tools/linear-agent/docs/PRE-RELEASE-DEPLOY-E2E.md`; ручной смоук `website/docs/manual-smoke-dev-to-prod.md`. Dev-хостинг = **https://dev.elmundi.com** (деплой с `main`), prod = ручной promote (workflows `bunny-promote-prod-*`).
- **Daily audit roles** (`tech-architect`, `qa-architect`, `security-officer`): не создавай тикеты без проверяемых фактов; дедупликация с открытыми issues в целевых проектах Linear. Маркер комментария: `[GitHub SDLC daily-audit:…]` (см. `docs/DAILY-AUDIT-ROLES.md`).

## Релевантные skills

{{SKILLS_CONTEXT}}
