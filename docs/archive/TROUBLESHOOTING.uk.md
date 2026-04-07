# Усунення несправностей

**Призначення:** симптом → де дивитися → дія.  
**Аудиторія:** оператори з доступом до GitHub і Linear.

| Симптом | Перевірка | Дія |
|---------|-----------|-----|
| **Pick issue** з `MISSING_LINEAR_API_KEY` | GitHub → Secrets → `LINEAR_API_KEY` | Додати repo/org secret; перезапустити workflow |
| **Cloud Agent** не стартує | `CURSOR_API_KEY` у secrets; доступ репо в Cursor | [Секрети Cursor Cloud](CLOUD-AGENT-SECRETS.md) |
| **Зелений SDLC run**, тикет лишається **Todo** | У минулому — некоректний state id команди; перевірити через CLI | `cd tools/linear-agent && node dist/cli.js start --issue ELM-XX --role developer` — очікуй **In Progress** |
| **Дублікати PR** для одного issue | Агент націлений на `fix/ELM-XX-auto` | Закрити зайві PR без merge; див. [Pre-release та E2E](PRE-RELEASE-DEPLOY-E2E.md) |
| **401** на exchange Bunny API | Неправильний тип ключа | `BUNNY_MAIN_API_KEY` за гайдом Pre-release |
| Пропуск **Snyk / security** | Немає `SNYK_TOKEN` | Очікувано: skip у логах; додати токен ([Щоденні аудити](DAILY-AUDIT-ROLES.md)) |
| **Незрозумілі черги** | Дошка Linear vs скрипт | `node scripts/agent-queue-snapshot.mjs` ([SDLC (розклад)](SDLC-AUTOMATION-SETUP.md)) |
| **Перевірка середовища** | Локально | `bash tools/linear-agent/scripts/verify-setup.sh` ([Автономний пайплайн](AUTONOMOUS-SETUP.md)) |

**Пов’язано:** [Каталог workflow](WORKFLOWS-CATALOG.md) · [Глосарій](GLOSSARY.md).
