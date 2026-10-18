# report-gen

Сборка отчётов по лабораторным в Word (оформление: Times New Roman 12, титульный лист ЕНУ).

```bash
cd report-gen && npm install
# рисунки — из реального вывода консоли в labN/results/console/
uv run --no-project --with pillow python make_figs_lab0.py ../lab0
# отчёт → ../lab0/Lab0_Жанайдаров_Амирлан.docx
node lab0.js ../lab0
# Lab 1: график модели строит ../lab1/tools/plot_model.py, затем
uv run --no-project --with pillow python make_figs_lab1.py ../lab1
node lab1.js ../lab1
```

- `lib.js` — общее оформление: титульный лист, заголовки, абзацы, таблицы, рисунки, вопросы.
- `labN.js` — содержание отчёта конкретной работы.
- `term_render.py` — отрисовка вывода команд в виде окна терминала.
