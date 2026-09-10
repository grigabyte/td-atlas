[English](README.md) | Русский | [简体中文](README.zh-CN.md)

<div align="center">
  <p align="center"><img src=".github/cover.png" width="720"
   alt="Знак td-atlas на поле волновых линий."></p>
  <h1>td-atlas</h1>
  <p>
    Разобранный на атомы индекс TouchDesigner, живой мост в запущенную
    программу и офлайновое чтение сохранённых проектов. Всё это отдано
    ИИ-агентам через MCP.
  </p>
  <p>
    <a href="#установка">Установка</a> ·
    <a href="#как-сервер-mcp">Как вызывать из агента</a> ·
    <a href="plugin/skills/touchdesigner/references/tools.md">Инструменты</a> ·
    <a href="docs/cli.md">Командная строка</a> ·
    <a href="docs/troubleshooting.md">Починка</a> ·
    <a href="CHANGELOG.md">Изменения</a>
  </p>
  <p>
    <a href="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml"><img src="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml/badge.svg" alt="CI: pytest и ruff на macOS и Windows"></a>
    <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 или новее на хосте">
    <img src="https://img.shields.io/badge/platform-macOS-lightgrey" alt="Платформа: macOS; код под Windows гоняется в CI, но TouchDesigner под Windows не проверялся">
    <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-blue" alt="Лицензия: MIT"></a>
  </p>
</div>


Агенту, который строит в TouchDesigner, нужны сразу три вещи: точное знание
операторов и параметров *этой* машины, управление запущенной программой
с откатом в один шаг и способ прочитать сохранённый проект, не открывая его.
Нет первого, и агент угадывает имена параметров. Нет второго, и сорвавшийся
шаг бросает сеть на полпути. Нет третьего, и любой вопрос про уже готовый
проект требует запущенной программы.

td-atlas стоит на двух наблюдениях:

1. **Почти всё, что агенту надо знать про TouchDesigner, уже лежит внутри
   самой программы.**
2. **TouchDesigner почти никогда не скажет, что что-то не так. Он просто
   ничего не покажет.**

## Что он делает

- **[Индекс атомов](docs/atom-index.md)** — два прохода дают один индекс
  SQLite, точный для той сборки, с которой его сняли, а не собранный из вики
  про какой-то другой выпуск.
- **[Мост](docs/bridge.md)** — Web Server DAT и DAT с обратными вызовами,
  которые строит скрипт, а не готовый `.tox`: мост читается глазами, ложится
  в diff и обновляется на месте.
- **[О чём TouchDesigner молчит](docs/health.md)** — `errors` берёт то, что
  TouchDesigner называет ошибкой; `td_health` берёт всё остальное.
- **[Журнал вызовов](docs/journal.md)** — строка на каждый вызов моста, пишет
  её хост, и она переживает сессию.
- **[Чтение проектов офлайн](docs/offline-projects.md)** — проект можно
  осмотреть, обыскать и сравнить без запущенного TouchDesigner, и оригинал
  при этом никто не трогает.
- **[Текст сети, записанный при сохранении](docs/network-text.md)** — мост
  умеет выписывать сеть текстом рядом с `.toe` при каждом сохранении, и у
  проекта появляется история в git.

## Установка

Сначала нужны три вещи.

- **TouchDesigner.** Индекс собирается из *вашей* копии программы и хранит те
  значения, которые она сообщает, так что скачивать нечего.
- **Python 3.11 или новее** на хосте.
- **ИИ-агент, который говорит на MCP**: инструменты вызывает он. Собрано
  и измерено на
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview), строка
  `claude mcp add` ниже это его команда; любой клиент, который говорит
  на [Model Context Protocol](https://modelcontextprotocol.io), достаёт те же
  инструменты. Вы говорите с агентом, агент говорит с TouchDesigner.

Про платформы и про то, что коннектор может изменить в вашем проекте, смотрите
[Совместимость](docs/compatibility.md).

Одной строкой, из терминала:

```bash
curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
```

`/i` это `install.sh` этого же репозитория под коротким именем: GitHub Pages
заново публикует файл из `main` при каждом пуше, который его меняет, поэтому
второй копии, которая могла бы отстать, тут нет. Когда Pages не отвечает, те же
самые байты приходят прямо из репозитория:

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) находит Python, клонирует репозиторий, делает рядом
виртуальное окружение, ставит пакет, собирает индекс и кладёт мост,
печатая каждую команду перед тем, как её выполнить. Он задаёт два вопроса:
куда клонировать и собирать ли индекс сейчас; когда спросить некого, берёт для
обоих значение по умолчанию. Сам td-atlas ложится в два места: в ту копию
репозитория, которую вы назвали, и в `~/.td-atlas`; кроме них `uv` или `pip`
наполняет свой кэш пакетов так же, как для любого пакета. Ни sudo, ни
системных каталогов, файлы запуска вашей оболочки он оставляет как есть.
Запустите его второй раз на уже существующей копии, и он её обновит: заново
ничего не начинается. Это скрипт POSIX-оболочки, поэтому под Windows берите
последовательность ниже.

<details>
<summary>Руками, и то же самое под Windows, шаг за шагом</summary>

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

Пакета на PyPI нет, поэтому `pip install td-atlas` и `uvx td-atlas` ничего
не найдут: скачанная копия репозитория и есть установка. Дальше из неё:

```bash
.venv/bin/td-atlas build      # offline index, 23–30 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

</details>

Ниже команда всюду записана коротко: `td-atlas`. Если виртуальное окружение
не включено, вызывайте её по пути (`.venv/bin/td-atlas`, на Windows
`.venv\Scripts\td-atlas`), иначе системный Python пакета не увидит.

`td-atlas install` печатает две строки, которые надо вставить. Первую:
в текстпорт TouchDesigner (Dialogs → Textport and DATs), один раз на проект.

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Вторую: строку `claude mcp add` для вашего MCP-клиента, про неё ниже. Ключ
`--write-mcp-json DIR` дополнительно запишет ту же запись в `DIR/.mcp.json`
или вольёт её в файл, который там уже лежит.

Теперь, когда TouchDesigner открыт и мост на месте, дополните индекс тем, что
известно только запущенной программе:

```bash
td-atlas probe
```

Дальше `td-atlas doctor`: только он говорит, встала ли установка. По каждому
звену, которое не `ok`, он называет починку и выходит с ненулевым кодом, если
что-то сломано. Поэтому он же скажет, на каком вы шаге, если пришли
с середины: на машине, где ничего не собрано, он отвечает
`index : FAIL … fix: td-atlas build` и `bridge : warn … fix: td-atlas install`.

## Как сервер MCP

Запустите `td-atlas install` и вставьте строку `claude mcp add …`, которую он
напечатает: она указывает на текущий интерпретатор абсолютным путём, поэтому
работает независимо от того, какой рабочий каталог у MCP-клиента и включено ли
какое-нибудь виртуальное окружение. Руками это подключают так:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

Дальше инструменты за агентом, обычные слова за вами. Ниже нет ни одной
команды для терминала: там то, что человек говорит агенту, и вызовы, в которые
это превращается:

| Что вы говорите агенту | Что он вызывает |
| --- | --- |
| *«Каким оператором сместить картинку шумом? Назови точные имена параметров, прежде чем что-нибудь строить.»* | `td_search_operators`, потом `td_operator_schema` |
| *«Собери в открытом у меня проекте noise, за ним blur, за ним out TOP, и проверь, что ничего не умерло молча.»* | `td_build`, вся пачка в одном блоке отмены, потом `td_health` |
| *«Похоже, ничего не происходит.»* | `td_health`, потом `td_flags` по тому, что он назовёт |
| *«Покажи, как это выглядит прямо сейчас, и движение за секунду.»* | `td_render`, а на движение контактный лист |
| *«Что внутри `/project1` в `myproject.toe`? TouchDesigner закрыт.»* | `td_project_read`: файл копируется в кэш и читается там |
| *«Что ты изменил с тех пор, как мы начали?»* | `td_snapshot` до и после, потом `td_project_diff` по этим двум; компоненты ложатся в `~/.td-atlas`, ваш собственный файл остаётся нетронутым |
| *«Отмени это.»* | `td_undo`: целая пачка `td_build` это один шаг |

41 инструмент в трёх группах: **9 по индексу**, они работают офлайн;
**23 живых**, они действуют в запущенной программе; **9 по файлам проекта**,
они читают и пишут `.toe`/`.tox` с диска. Каждый из них, с аргументами и с
тем, зачем он нужен, перечислен в
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md):
один список, привязанный к коду тестом. Второй копии здесь нет, она бы
разъехалась.

`td_build` и `td_set_params` сверяют имена параметров с индексом до отправки,
поэтому обычные ошибки возвращаются поправками:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry
        (try: simplex4d, simplex3d, simplex2d, sparse, perlin4d)
- period: -3 is below the clamped minimum 0.0
```

## Документация

| Документ | Кому |
| --- | --- |
| [`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md) | агентам, которые *пользуются* коннектором |
| [`plugin/skills/touchdesigner/references/gotchas.md`](plugin/skills/touchdesigner/references/gotchas.md) | все ловушки, которые не дали ни одной ошибки |
| [`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md) | все 41 инструмент MCP |
| [`AGENTS.md`](AGENTS.md) | агентам, которые *вносят правки* в этот репозиторий |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | как прогнать тесты и линтер перед пул-реквестом |
| [`CHANGELOG.md`](CHANGELOG.md) | что изменилось в каждой версии, и каждая смена протокола обязательно |
| [`docs/architecture.md`](docs/architecture.md) | как три слоя складываются вместе и почему |
| [`docs/cli.md`](docs/cli.md) | каждая подкоманда и каждый ключ `td-atlas`, и что каждому нужно |
| [`docs/formats.md`](docs/formats.md) | формат `.toe`/`.tox`, разобранный обратной инженерией, с уликами |
| [`docs/atom-index.md`](docs/atom-index.md) | два прохода, которые собирают индекс, и что даёт каждый источник |
| [`docs/bridge.md`](docs/bridge.md) | компонент, который живёт внутри TouchDesigner, и что он добавляет сверх `exec` |
| [`docs/health.md`](docs/health.md) | о чём TouchDesigner молчит и что вместо него печатает `td_health` |
| [`docs/journal.md`](docs/journal.md) | журнал вызовов: что записывается, кем, и что в него не попадает |
| [`docs/offline-projects.md`](docs/offline-projects.md) | как читать, искать и сравнивать `.toe` при закрытом TouchDesigner |
| [`docs/network-text.md`](docs/network-text.md) | текст рядом с `.toe`, который ложится в diff, и семь известных расхождений |
| [`docs/compatibility.md`](docs/compatibility.md) | сборки, Python, операционные системы и что это может изменить в вашем проекте |
| [`docs/skill.md`](docs/skill.md) | навык для агента и его установка как плагина |
| [`docs/bundle.md`](docs/bundle.md) | сборка `.mcpb` и что означала бы его публикация |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | каждый симптом, что это такое и что запустить |
| [`docs/development.md`](docs/development.md) | набор тестов и инвариант, который он держит |
| [`docs/layout.md`](docs/layout.md) | каждый каталог репозитория и что в нём лежит |

## Лицензия

MIT, смотрите [LICENSE](LICENSE). TouchDesigner это продукт Derivative Inc.;
этот проект с ними не связан и ничего из установки не раздаёт, он только
читает то, что уже лежит на вашей машине.
