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

td-atlas даёт агенту три вещи. Точные имена операторов и параметров из
*вашей* копии программы. Руки внутри запущенной программы, с откатом в один шаг. И способ
прочитать сохранённый проект, не открывая его.

## Что он делает

- **[Индекс атомов](docs/atom-index.md)** собирает за два прохода один индекс
  SQLite, с вашей собственной копии программы. Значения в нём те, о которых
  сообщает эта копия.
- **[Мост](docs/bridge.md)** это Web Server DAT и DAT с обратными вызовами,
  которые скрипт строит в вашем проекте. Его можно прочитать глазами, увидеть
  его правки в git и обновить на месте.
- **[О чём TouchDesigner молчит](docs/health.md)**. `errors` берёт то, что
  TouchDesigner сам называет ошибкой, а `td_health` берёт поломки, о которых
  он молчит.
- **[Журнал вызовов](docs/journal.md)** пишет строку на каждый вызов моста, на
  стороне хоста, и переживает сессию.
- **[Чтение проектов офлайн](docs/offline-projects.md)** осматривает, обыскивает
  и сравнивает проект при закрытом TouchDesigner, и оригинал файла остаётся
  в покое.
- **[Текст сети, записанный при сохранении](docs/network-text.md)** мост умеет
  класть рядом с `.toe` при каждом сохранении, и у проекта появляется история
  в git, которая ложится в diff.

## Установка

Сначала нужны три вещи.

- **TouchDesigner**, уже установленный. [Индекс](docs/atom-index.md) снимается
  с *вашей* копии программы и держит те значения, о которых она сообщает.
  Скачивать нечего.
- **Python 3.11 или новее**, на хосте.
- **ИИ-агент, который говорит на [MCP](https://modelcontextprotocol.io)**. Это
  он вызывает наши инструменты. td-atlas собран и промерен на
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview), и его
  команда это строка `claude mcp add` ниже. Любой клиент MCP дотягивается до
  тех же инструментов.

[Совместимость](docs/compatibility.md) перечисляет платформы и то, что
коннектор может изменить в вашем проекте.

Одна строка из терминала:

```bash
curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
```

Если этот адрес не отвечает, тот же скрипт выдаёт репозиторий:

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) находит Python, клонирует репозиторий, делает рядом
virtualenv, ставит пакет, собирает индекс и раскладывает [мост](docs/bridge.md).
Каждая команда печатается до того, как выполнится.

Он задаёт два вопроса: куда клонировать и собирать ли индекс сейчас. Когда
терминала для вопроса нет, он берёт для обоих значение по умолчанию.

На диске оказываются два каталога: клон, который вы назвали, и `~/.td-atlas`.
Кроме них `uv` или `pip` наполняет свой кеш пакетов, как делает это для любого
пакета. Sudo нет. Системные каталоги и стартовые файлы вашей оболочки остаются
в покое. Запустите его снова на готовом клоне, и он обновит этот клон.

Это скрипт для оболочки POSIX. На Windows идёт последовательность ниже.

<details>
<summary>Руками, а также на Windows: та же последовательность по шагам</summary>

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

Пакета на PyPI нет. `pip install td-atlas` и `uvx td-atlas` ничего не найдут.
Дальше, из клона:

```bash
.venv/bin/td-atlas build      # offline index, 13–16 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

</details>

Дальше команды пишутся коротко, `td-atlas`. Пока virtualenv не активирован,
зовите её по пути. Это `.venv/bin/td-atlas` или `.venv\Scripts\td-atlas` на
Windows. Системный Python пакета не видит.

`td-atlas install` печатает две строки, которые надо вставить. Первую в
текстпорт TouchDesigner (Dialogs → Textport and DATs), один раз на проект:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Вторая это строка `claude mcp add` для вашего клиента MCP, про неё
[Как сервер MCP](#как-сервер-mcp). Ключ `--write-mcp-json DIR` заодно запишет
ту же запись в `DIR/.mcp.json` или вольёт её в файл, который там уже лежит.

Когда TouchDesigner открыт и мост разложен, доведите индекс:

```bash
td-atlas probe
```

Этот проход добавляет факты, которые знает только запущенная программа.

Последнее: проверьте установку. Если вы вошли с середины, начните отсюда.

```bash
td-atlas doctor
```

У каждого звена, кроме `ok`, рядом напечатана починка, и сломанное звено даёт
ненулевой код возврата. На хосте, где ничего не собрано, вы увидите
`index : FAIL … fix: td-atlas build` и `bridge : warn … fix: td-atlas install`.

## Как сервер MCP

Запустите `td-atlas install` и вставьте строку `claude mcp add …`, которую он
напечатает. Строка называет интерпретатор абсолютным путём, поэтому работает из
любого рабочего каталога, с включённым virtualenv и без него. Руками это
подключают так:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

Дальше вы говорите обычными словами, что вам нужно. Вот во что агент это
превращает:

| Что вы говорите агенту | Что он вызывает |
| --- | --- |
| *«Каким оператором сместить картинку шумом? Назови точные имена параметров, прежде чем что-нибудь строить.»* | `td_search_operators`, потом `td_operator_schema` |
| *«Собери в открытом у меня проекте noise, за ним blur, за ним out TOP, и проверь, что ничего молча не умерло.»* | `td_build`, вся пачка в одном блоке отмены, потом `td_health` |
| *«Похоже, ничего не происходит.»* | `td_health`, потом `td_flags` по тому, что он назовёт |
| *«Покажи, как это выглядит прямо сейчас, и движение за секунду.»* | `td_render`, а на движение контактный лист |
| *«Что внутри `/project1` в `myproject.toe`? TouchDesigner закрыт.»* | `td_project_read`, файл копируется в кеш и читается там |
| *«Что ты изменил с тех пор, как мы начали?»* | `td_snapshot` до и после, потом `td_project_diff` по этим двум; компоненты ложатся в `~/.td-atlas`, ваш собственный файл никто не трогает |
| *«Отмени это.»* | `td_undo`, целая пачка `td_build` идёт одним шагом |

41 инструмент в трёх группах. **9 по индексу** работают офлайн, **23 живых**
действуют в запущенной программе, **9 по файлам проекта** читают и пишут
`.toe`/`.tox` с диска. Каждый, с аргументами и с тем, зачем он нужен, лежит в
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md),
и тест держит этот список у кода.

`td_build` и `td_set_params` сверяют имена параметров с индексом до отправки,
поэтому обычные промахи возвращаются поправками:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry
        (try: simplex4d, simplex3d, simplex2d, sparse, perlin4d)
- period: -3 is below the clamped minimum 0.0
```

## Документация

| Документ | Кому и зачем |
| --- | --- |
| [`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md) | агентам, которые *пользуются* коннектором |
| [`plugin/skills/touchdesigner/references/gotchas.md`](plugin/skills/touchdesigner/references/gotchas.md) | каждая ловушка, которая не дала ни одной ошибки |
| [`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md) | все 41 инструмент MCP |
| [`AGENTS.md`](AGENTS.md) | агентам, которые *вносят правки* в этот репозиторий |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | как прогнать тесты и линтер перед пул-реквестом |
| [`CHANGELOG.md`](CHANGELOG.md) | что изменилось в каждой версии, и каждая смена протокола обязательно |
| [`docs/architecture.md`](docs/architecture.md) | как три слоя складываются вместе и почему |
| [`docs/cli.md`](docs/cli.md) | каждая подкоманда и каждый ключ `td-atlas`, и что каждому нужно |
| [`docs/formats.md`](docs/formats.md) | формат `.toe`/`.tox`, разобранный обратной инженерией, с уликами |
| [`docs/atom-index.md`](docs/atom-index.md) | два прохода, которые собирают индекс, и что даёт каждый источник |
| [`docs/bridge.md`](docs/bridge.md) | компонент, который работает внутри TouchDesigner, и что он добавляет сверх `exec` |
| [`docs/health.md`](docs/health.md) | о чём TouchDesigner молчит и что вместо него печатает `td_health` |
| [`docs/journal.md`](docs/journal.md) | журнал вызовов: что пишется, кем, и что в него не попадает |
| [`docs/offline-projects.md`](docs/offline-projects.md) | как читать, искать и сравнивать `.toe` при закрытом TouchDesigner |
| [`docs/network-text.md`](docs/network-text.md) | текст рядом с `.toe`, который ложится в diff, и семь известных расхождений |
| [`docs/compatibility.md`](docs/compatibility.md) | сборки, Python, операционные системы и что это может изменить в вашем проекте |
| [`docs/skill.md`](docs/skill.md) | навык для агента и его установка как плагина |
| [`docs/bundle.md`](docs/bundle.md) | сборка `.mcpb` и что означала бы его публикация |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | каждый симптом, что это такое и что запустить |
| [`docs/development.md`](docs/development.md) | набор тестов и инвариант, который он держит |
| [`docs/layout.md`](docs/layout.md) | каждый каталог репозитория и что в нём лежит |

## Лицензия

MIT, полный текст лежит в [LICENSE](LICENSE). TouchDesigner это продукт
Derivative Inc. Наш проект с ними никак не связан и ничего из установленной
программы не раздаёт. Он только читает то, что уже есть на вашей машине.
