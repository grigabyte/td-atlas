# td-atlas

Атомарный индекс TouchDesigner, живой мост в запущенный экземпляр и
офлайн-читатель проектов — для ИИ-агентов через MCP.

## Запуск и сборка

```bash
uv pip install -e . pytest
pytest                  # без TouchDesigner
td-atlas build           # офлайн-индекс
td-atlas probe            # нужен запущенный TouchDesigner с мостом
```
Правка кода — по `AGENTS.md`, это руководство контрибьютора, здесь оно
не пересказывается.

## Инварианты

Измерять, а не угадывать. Тесты не требуют запущенного TouchDesigner.
При выборе между уверенным неверным ответом и честным пробелом —
пробел. Полный реестр с датами — `memory-bank/decisions.md`.

## С чего начать

`memory-bank/handoff.md` → `memory-bank/state.md` →
`memory-bank/plan.md` → `memory-bank/forks.md`. Задачи о содержании
проекта — `memory-bank/steering/`.

Жанры и контракты банка — `memory-bank/README.md`. Файлы памяти
обновляются в том же цикле, что и работа.
