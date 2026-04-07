# Ship

**Версія** 0.6.0 — див. [юридичний блок](legal-copyright.md) (мітка також у шапці сайту).

**Ship** — це фреймворк; цей сайт — його посібник. Задуманий як **коротка книга**: чітка думка, лінійне читання, **розділення навмисне** — **Framework**, далі **Prompts & workflows**, **Tools**, **Examples** (одна повна **референс-проводка** в `examples/elmundi/` — замініть на свої значення у практиці).

Після публікації вистав у **`mkdocs.yml` → `site_url`** канонічний URL під свій хост. Доки ні — збирай локально (нижче).

---

## Читай у такому порядку

1. **[Framework](framework/index.md)** — **одна довга сторінка**: відкрий вкладку й скроль.  
2. Усередині Framework: [Ідея](framework/index.md#the-idea) · [Система](framework/index.md#the-system) · [Цикл](framework/index.md#running-the-loop) — або просто скроль.  
3. **[Prompts & workflows → Ітерації промптів](prompts-workflows/iterating-on-prompts.md)** — звичка, яка перетворює поганий запуск на кращий промпт **без** втрати аудиту.

Потім відкрий **[Examples → Reference org](examples/elmundi/index.md)** — **імена файлів, хвилини cron, домени, секрети**, тобто глава «чеки».

---

## Чотири вкладки зверху

| Вкладка | Коли відкривати |
|---------|-----------------|
| **Framework** | Потрібен **патерн** — переносний, без прив’язки до вендора. |
| **Prompts & workflows** | Як **еволюціонують промпти** і для чого кожен **клас** workflow. |
| **Tools** | Що **підключається** (Linear, GitHub Actions, Cursor agent, Playwright, Snyk, …). |
| **Examples** | **Референс-проводка** (YAML, Linear, cron, домени, секрети) — шлях `examples/elmundi/`. |

---

## Локальна збірка

```bash
cd tools/linear-agent
python3 -m venv .venv-docs
source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve
```

**PDF:** [PDF та офлайн](pdf-export.md).

---

## Право

[Юридична інформація](legal-copyright.md).
