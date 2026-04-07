# PDF та офлайн

Цей сайт документує **Ship**. Канонічний URL публікації — у **`mkdocs.yml` → `site_url`**. Збірка: [MkDocs](https://www.mkdocs.org) і [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/). **Англійська** збирається в `site/`; **українська** (`*.uk.md`) — у `site/uk/`.

## Статичний сайт (офлайн)

```bash
cd tools/linear-agent
pip install -r requirements-docs.txt
mkdocs build
```

Відкрий `tools/linear-agent/site/index.html` (і `site/uk/index.html` для української). Локальний сервер не обов’язковий: `cd site && python -m http.server`.

## Друк / PDF з браузера

Окремої злитої сторінки «повний мануал» більше немає. Після `mkdocs serve` або `mkdocs build` відкрий потрібні сторінки (наприклад **[Framework](framework/index.md)** — один довгий скроль) і використай **Друк → Зберегти як PDF** (Chrome / Edge / Safari).

### Поради щодо якості PDF

- Перед друком краще **світла** палітра.
- Увімкни **фонові зображення**, якщо діаграми обрізаються.
- Українська збірка: у `site/uk/` — запускай друк з сайту з префіксом `/uk/`, якщо потрібен лише UK PDF.

## Двомовні збірки

`mkdocs-static-i18n` створює окремі дерева. Друкуй потрібну мову з відповідного базового URL.

## Залежності

Ізолюй Python-залежності:

```bash
python3 -m venv .venv-docs
source .venv-docs/bin/activate
pip install -r requirements-docs.txt
```

## Діаграми D2

Джерела: `docs/diagrams/*.d2`. SVG перегенеровуються під час збірки, якщо CLI **`d2`** є в `PATH`; інакше коміть SVG (уже в репо після першого рендеру).

---

© Див. [Юридична інформація](legal-copyright.md).
