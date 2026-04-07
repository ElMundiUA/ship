## Сводка треда (после консолидации)

**Задача:** единый визуальный стиль всех ячеек шапки **Detailed plan comparison** на `/pricing` (**Feature**, **Monthly**, **Annual**). У **Annual** убрать featured-only оформление **только в шапке таблицы сравнения**; карточки планов (`plan--featured`), логика цен, строки и маркеры таблицы — без изменений.

**Файлы:** `website/app/pricing/page.tsx`, `website/styles/pricing.css` — не использовать `comparison__head--featured` в шапке comparison-grid.

**Доставка:** PR https://github.com/ElMundiUA/elmundi/pull/34 — реализация и проверки (lint, typecheck, tests, build, e2e smoke) по отчёту в исходном треде.

**Статус в Linear:** Done. Полные AC, edge cases и test plan — в **описании issue**; ниже этой заметки старые комментарии удалены как дубли автоматизации (intake, BA, clarification, preview recovery, повторные блоки Feature Description / Ready for Human Validation).
