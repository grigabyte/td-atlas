// Генератор сайта документации td-atlas. Раунд 5, замок d1.
//
//   node site/generator/sborka.mjs                 всё: лендинг (vite) + доки
//   node site/generator/sborka.mjs --tolko-doki    только доки (из npm build)
//   node site/generator/sborka.mjs --fixtura <кат> фикстура пункта 2 (три
//                     страницы замка на старом снимке и текстах веера)
//   node site/generator/sborka.mjs --sverit-veer   фикстура против
//                     <снимок>/../веер/d1 побайтно (кроме шапки-комментария)
//   ключи: --istochnik <кат>  источник английского текста; умолчание —
//                     КОРЕНЬ РЕПОЗИТОРИЯ (рабочее дерево, решение 11.09).
//                     Каталог-снимок с КОММИТ.txt тоже принимается —
//                     им собирается фикстура пункта 2
//          --ru <кат>         RU-переводы (умолчание <корень>/docs/ru)
//
// Страницы руками не правятся — правится этот файл.
//
// Что собирается: 21 раздел полного зеркала (таблица RAZDELY ниже,
// «Источник правды» ПЛАН-раунд-5) плюс страница-указатель /docs/.
// EN — текст источника как есть; RU — перевод из --ru, а где его нет,
// страница остаётся английской с плашкой. Оглавление страницы — из
// НАСТОЯЩИХ H2/H3 источника (решение владельца 08.09): подзаголовков
// генератор не сочиняет.
//
// Языки: один адрес, устройство лендинга (?lang= → localStorage →
// navigator.language), выбор в инлайновом скрипте <head>, подстановка
// до первого кадра. В ДЕРЕВЕ ДОКУМЕНТА ЛЕЖИТ РОВНО ОДИН ЯЗЫК: второй
// живёт в <template>, содержимое которого в document.querySelectorAll
// не попадает (иначе ворота 2а удвоили бы кнопки, а 14 — id заголовков).
import {
  readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, copyFileSync, rmSync,
} from 'node:fs'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve, relative } from 'node:path'
import { razbor, slugGitHub, slug, bezShapki, esc } from './md.mjs'
import {
  CSS_D1, CSS_ZHIVOJ, stranica, shapka, menu, oglavlenie, prozaHTML,
  KNOPKA_ZHIVAJA, METKI_INSTR_RU, METKI_INSTR_EN,
} from './shablon.mjs'
import { fikstura } from './fixtura.mjs'
import { adres } from '../baza.mjs'

const TUT = dirname(fileURLToPath(import.meta.url))     // site/generator
const SAJT = resolve(TUT, '..')                        // site
const REPO = resolve(SAJT, '..')                       // корень репозитория

// ── ключи ────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2)
const klyuch = (n, d = null) => {
  const i = argv.indexOf('--' + n)
  return i === -1 ? d : argv[i + 1]
}
const flag = n => argv.includes('--' + n)
// ПЕРЕЕЗД 11.09, решение владельца: английский текст берётся из РАБОЧЕГО
// ДЕРЕВА репозитория, а не из снимка под `~/Documents`. Умолчание `IST` —
// корень репозитория (два уровня вверх от `site/generator/`), умолчание
// `RU_DIR` — `<корень>/docs/ru`. Ключи `--istochnik` и `--ru` сохранены:
// ими фикстура пункта 2 по-прежнему собирается на старом снимке
// `fanout/раунд-5/istochnik/` рабочей папки лендинга.
const IST = resolve(klyuch('istochnik', REPO))
const RU_DIR = resolve(klyuch('ru', join(REPO, 'docs', 'ru')))
const FIX_DIR = klyuch('fixtura')
// Каталог волны рядом со снимком: по нему ищутся прежние снимки переводов
// и HTML веера для сверки. При работе на репозитории его нет — и не нужно.
const VOLNA = resolve(IST, '..')

// Источник бывает двух родов: СНИМОК (каталог с `КОММИТ.txt` — так
// собирается фикстура пункта 2) и РАБОЧЕЕ ДЕРЕВО репозитория (каталог
// с `.git` — так собирается сайт с 11.09). Нет ни того, ни другого —
// хеша и даты нет, плашка встаёт без даты.
const SNIMOK = existsSync(join(IST, 'КОММИТ.txt'))
const GIT_DEREVO = !SNIMOK && existsSync(join(IST, '.git'))
const git = (...a) => {
  try { return execFileSync('git', ['-C', IST, ...a], { encoding: 'utf-8' }).trim() }
  catch { return '' }
}
// Ссылки на GitHub строятся по ПОЛНОМУ хешу: короткая форма в первой
// строке КОММИТ.txt однозначна только в этом чекауте. Полный хеш назван
// в файле строкой «полный хеш: …»; её нет — берётся первая длинная
// шестнадцатеричная строка, а её нет — короткая.
// На рабочем дереве хеш берётся из git — короткий, как записано
// в контракте переезда: пункт 14 приёмки проверяет у blob-ссылки путь,
// а не ref.
const KOMMIT = SNIMOK
  ? (() => {
      const t = readFileSync(join(IST, 'КОММИТ.txt'), 'utf-8')
      const m = t.match(/полный хеш:\s*([0-9a-f]{40})/) || t.match(/\b[0-9a-f]{40}\b/)
        || t.match(/[0-9a-f]{7,40}/)
      return m ? (m[1] || m[0]) : 'HEAD'
    })()
  : (GIT_DEREVO && git('rev-parse', '--short', 'HEAD')) || 'HEAD'
const GITHUB = 'https://github.com/grigabyte/td-atlas'
const heshFajla = t => createHash('sha256').update(String(t).replace(/\r/g, ''), 'utf8').digest('hex')

// ═════════════════════════════════════════════════════════════════════════
//  21 раздел зеркала. Слаг адреса обязан совпадать с таблицей RAZDELY
//  внутри tools/priemka-doki.mjs — по нему пункт 7 находит файл-источник.
//
//  Доводка 2 (09.09), правка 1: слаги АНГЛИЙСКИЕ. Русский транслит
//  («lovushki», «resheniya», «ustanovka») в адресах при английском главном
//  языке — слово владельца 09.09. Прибор эти имена уже знает: у каждого
//  раздела в его таблице записан английский синоним (`install`, `tools`,
//  `gotchas`, …), так что пункт 7 находит файл-источник по-прежнему.
//
//  Правка 2: разделы «Решения» (docs/decisions.md) и «Публикация»
//  (docs/publishing.md) сняты со страницы — «юзеру не нужны», слово
//  владельца 09.09. Ссылки источников на эти два файла теперь уходят
//  на GitHub по хешу снимка, как всякий файл вне зеркала.
// ═════════════════════════════════════════════════════════════════════════
//
//  10.09: РЕПОЗИТОРИЙ ПЕРЕОФОРМЛЕН. README разрезан (703 строки и 16 H2 →
//  234 строки и 5 H2), двенадцать разделов уехали в docs/ отдельными
//  файлами. Разделов сайта стало 21 вместо 9: файл источника = раздел.
//  Порядок меню — группировка САМОГО README (старый навигационный блок
//  «What it does · Getting it running · Working on it», istochnik-0f6ea8e,
//  строки 19–37), а не вкус строителя:
//    1        обзор и установка             README.md
//    2–7      что он делает                 шесть уехавших файлов, в порядке README
//    8–11     поставить и починить          compatibility, skill, bundle, troubleshooting
//    12–17    справочник                    cli, tools, gotchas, SKILL, architecture, formats
//    18–21    работа над репозиторием       development, layout, AGENTS+CONTRIBUTING, CHANGELOG
//
//  ИМЕНА СЛАГОВ. Прибор (tools/priemka-doki.mjs) ищет раздел подстрокой
//  пути: `p.includes(slug)`, длиннейшее совпадение выигрывает. Ни один из
//  двенадцати новых слагов не содержит ни одного слага его таблицы, иначе
//  пункт 7 сверял бы числа новой страницы с чужим файлом. Поэтому
//  docs/skill.md — это `plugin` («Как плагин»), а не `skill`: слаг со
//  словом `skill` увёл бы его числа в plugin/…/SKILL.md. Цена: двенадцати
//  новых разделов прибор не знает вовсе и сверяет их числа с ОБЪЕДИНЕНИЕМ
//  снимка — проверка слабее адресной; лечится таблицей внутри tools/,
//  а он вне границ этой смены.
//
//  Имя пункта меню — заголовок самого документа, кроме одного случая:
//  «What TouchDesigner does not report» в колонку 236 px не влезает и
//  занимает две строки, отчего меню из 21 пункта перестаёт помещаться
//  в окно 1440×900. В меню он назван словарём самого репозитория:
//  `td_health` там «the silent-failure detector» (docs/bridge.md),
//  по-русски «ищет поломки, о которых программа молчит».
// ═════════════════════════════════════════════════════════════════════════
const RAZDELY = [
  // обзор и установка
  { klyuch: 'install', fajly: ['README.md'], ru: 'Установка', en: 'Install' },
  // что он делает — порядок README
  { klyuch: 'atom-index', fajly: ['docs/atom-index.md'], ru: 'Индекс атомов', en: 'The atom index' },
  { klyuch: 'bridge', fajly: ['docs/bridge.md'], ru: 'Мост', en: 'The bridge' },
  { klyuch: 'health', fajly: ['docs/health.md'], ru: 'Молчаливые поломки', en: 'Silent failures' },
  { klyuch: 'journal', fajly: ['docs/journal.md'], ru: 'Журнал вызовов', en: 'The call journal' },
  { klyuch: 'offline-projects', fajly: ['docs/offline-projects.md'], ru: 'Проекты офлайн', en: 'Projects offline' },
  { klyuch: 'network-text', fajly: ['docs/network-text.md'], ru: 'Текст сети', en: 'The network text' },
  // поставить и починить
  { klyuch: 'compatibility', fajly: ['docs/compatibility.md'], ru: 'Совместимость', en: 'Compatibility' },
  { klyuch: 'plugin', fajly: ['docs/skill.md'], ru: 'Как плагин', en: 'As a plugin' },
  { klyuch: 'bundle', fajly: ['docs/bundle.md'], ru: 'Как бандл', en: 'As a bundle' },
  { klyuch: 'troubleshooting', fajly: ['docs/troubleshooting.md'], ru: 'Починка', en: 'Troubleshooting' },
  // справочник
  { klyuch: 'commands', fajly: ['docs/cli.md'], ru: 'Команды', en: 'Commands' },
  { klyuch: 'tools', fajly: ['plugin/skills/touchdesigner/references/tools.md'], ru: 'Инструменты', en: 'Tools' },
  { klyuch: 'gotchas', fajly: ['plugin/skills/touchdesigner/references/gotchas.md'], ru: 'Ловушки без ошибок', en: 'Gotchas' },
  { klyuch: 'skill', fajly: ['plugin/skills/touchdesigner/SKILL.md'], ru: 'Скилл для агента', en: 'Agent skill' },
  { klyuch: 'architecture', fajly: ['docs/architecture.md'], ru: 'Устройство', en: 'Architecture' },
  { klyuch: 'formats', fajly: ['docs/formats.md'], ru: 'Формат .toe/.tox', en: '.toe/.tox format' },
  // работа над репозиторием
  { klyuch: 'development', fajly: ['docs/development.md'], ru: 'Разработка', en: 'Development' },
  { klyuch: 'layout', fajly: ['docs/layout.md'], ru: 'Устройство каталогов', en: 'Layout' },
  { klyuch: 'contributing', fajly: ['AGENTS.md', 'CONTRIBUTING.md'], ru: 'Для контрибьюторов', en: 'For contributors' },
  { klyuch: 'changelog', fajly: ['CHANGELOG.md'], ru: 'Что изменилось', en: 'Changelog' },
]
// файл источника → адрес раздела на сайте (для переписывания ссылок)
const PO_FAJLU = new Map()
for (const r of RAZDELY) for (const f of r.fajly) PO_FAJLU.set(f, r)

// Дата источника для плашки перевода (правка 5 доводки 09.09). Плашка
// обязана называть возраст ТЕКСТА, а не время запуска генератора, поэтому
// дата берётся у источника, а не у часов:
//   снимок        — дата из КОММИТ.txt;
//   рабочее дерево — дата последнего коммита, ТРОНУВШЕГО файлы разделов
//                    (`git log -1 --format=%cs -- <файлы RAZDELY>`);
//   ни то, ни другое — null, плашка без даты.
// Формат замка — «дд.мм» («Перевод английской документации от 07.09»).
const DATA_ISTOCHNIKA = (() => {
  const vDdMm = s => {
    const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})/)
    return m ? `${m[3]}.${m[2]}` : null
  }
  if (SNIMOK) return vDdMm(readFileSync(join(IST, 'КОММИТ.txt'), 'utf-8'))
  if (!GIT_DEREVO) return null
  return vDdMm(git('log', '-1', '--format=%cs', '--', ...[...PO_FAJLU.keys()]))
})()

// ═════════════════════════════════════════════════════════════════════════
//  ХРОМ: строки, которые пишет генератор. Оба языка, ключ = data-t.
//  Утверждений о продукте здесь нет — только навигация и подписи кнопок.
//  Длинного тире в русских строках нет (п. 11, правило 12 банлиста).
// ═════════════════════════════════════════════════════════════════════════
const HROM = {
  'hdr.docs': { ru: 'Документация', en: 'Documentation' },
  'hdr.cta': { ru: 'Попробовать', en: 'Try it' },
  'hdr.s2': { ru: 'Находит ошибки', en: 'Spots errors' },
  'hdr.s3': { ru: 'Правит сеть', en: 'Edits blocks' },
  'hdr.s4': { ru: 'Смотрит картинку', en: 'Looks at frames' },
  'hdr.s5': { ru: 'Установка', en: 'Install' },
  'menu.eyebrow': { ru: 'разделы', en: 'sections' },
  'menu.zakryt': { ru: 'Закрыть', en: 'Close' },
  'polosa.razdely': { ru: 'Разделы', en: 'Sections' },
  'ogl.eyebrow': { ru: 'на странице', en: 'on this page' },
  'ogl.aria': { ru: 'на этой странице', en: 'on this page' },
  kopir: { ru: 'Скопировать', en: 'Copy' },
  'kopir.gotovo': { ru: 'Скопировано', en: 'Copied' },
  'ukaz.h1': { ru: 'Документация', en: 'Documentation' },
  'ukaz.abzac': {
    ru: 'Зеркало документации репозитория td-atlas. Ниже разделы и файлы, из которых они собраны.',
    en: 'A mirror of the td-atlas repository documentation. Below: the sections and the files they are built from.',
  },
  'ukaz.eyebrow': { ru: 'разделы', en: 'sections' },
  'plashka.net': { ru: 'Этой страницы по-русски пока нет.', en: '' },
  'plashka.otstal': { ru: 'Он отстаёт от английского', en: '' },
  // В замке d1 ссылка подписана «English» в обоих языках — так и осталось.
  'plashka.ssylka': { ru: 'English', en: 'English' },
  // Правка 5 доводки 09.09: строка замка d1, потерянная генератором волны.
  // В замке она стоит на каждой русской странице первой строкой прозы.
  'plashka.perevod': {
    ru: DATA_ISTOCHNIKA ? `Перевод английской документации от ${DATA_ISTOCHNIKA}` : 'Перевод английской документации',
    en: '',
  },
  // Подпись к таблице «Инструменты»: имена и описания в ней английские
  // по решению владельца 08.09. Говорит про содержимое таблицы
  // (ГОЛОС-ДОКОВ.md, правило 7: мета-фраз о самом тексте нет).
  'plashka.instr': { ru: 'Имена и описания в таблице стоят по-английски, как их видит агент.', en: '' },
  // Раунд 8, находка L1: подпись-признак прокрутки у схемы шире экрана.
  // Цифр в ней нет (пункт 7 читает прозу без кода, подпись туда попадает),
  // длинного тире нет (правило 12 банлиста). Стрелка U+2192 вне диапазона
  // правила 8 check.sh (U+1F000–1FAFF и U+2600–27BF).
  'shema.shirokaya': { ru: 'шире экрана, прокрутите вбок →', en: 'wider than the screen, scroll →' },
}
for (const r of RAZDELY) HROM['razdel.' + r.klyuch] = { ru: r.ru, en: r.en }

// ═════════════════════════════════════════════════════════════════════════
//  Чтение источника и разбор
// ═════════════════════════════════════════════════════════════════════════
const chit = f => readFileSync(join(IST, f), 'utf-8').replace(/\r/g, '')

// ── сырой HTML источника: что на сайт не идёт ────────────────────────────
// 10.09, переоформление репозитория. У README появилась HTML-шапка:
// первая строка с переключателем языков плюс <div align="center"> с <h1>,
// абзацем-подзаголовком, шестью ссылками и четырьмя <img>-значками, и следом
// HTML-комментарий со слотом под скринкаст.
//
// БЛОК ВЫРЕЗАЕТСЯ ЦЕЛИКОМ, до </div>. Это не вкус, а четыре правила:
//   1. «наружу не ходим» (ПРИЁМКА.md, п. 14): четыре значка — это четыре
//      <img> с двух чужих хостов (img.shields.io, github.com/…/badge.svg),
//      а спека раунда 5 прямо говорит «п. 5 (картинки) не применяется —
//      на доках изображений нет»;
//   2. хром страницы приходит из шаблона, а не из источника: шесть ссылок
//      блока дублируют меню разделов, <h1> дублирует заголовок страницы,
//      а первая строка (English | Русский | 简体中文) — свой переключатель
//      EN·RU в шапке. Оставленная, она стала бы абзацем со ссылкой на
//      README.ru.md, то есть уходом на GitHub с двуязычной страницы;
//   3. в md.mjs разбора HTML нет и вводить его запрещено («новых правил
//      разметки не вводить»): «как есть» означало бы шестнадцать строк
//      экранированного &lt;div align="center"&gt; посреди прозы;
//   4. единственная проза блока — подзаголовок «An atomised index…» —
//      дословно повторяет первый экран лендинга, который стоит этажом выше.
// Комментарий-слот вырезать не надо: razbor уже пропускает HTML-комментарии.
//
// <details>/<summary> внутри «Install» — второй сырой HTML того же коммита.
// Раскрывающийся блок означал бы новый интерактивный узел (ворота 2а
// считают кнопки) и новое правило разметки. Теги снимаются, подпись
// <summary> остаётся обычным абзацем — ровно ту роль она и играла до
// переоформления («By hand is the same sequence:», istochnik-0f6ea8e).
//
// Хеш EN-файла для шапки перевода (п. 15) считается по СЫРОМУ файлу,
// не по вычищенному: рельс читает файл снимка сам.
function chistka(fajl, md) {
  let t = md
  if (fajl === 'README.md') {
    const konec = t.indexOf('</div>')
    if (konec !== -1) t = t.slice(konec + '</div>'.length).replace(/^\n+/, '')
  }
  return t.split('\n')
    .map(l => {
      const m = l.match(/^\s*<summary>(.*)<\/summary>\s*$/)
      if (m) return m[1]
      return /^\s*<\/?details>\s*$/.test(l) ? '' : l
    })
    .join('\n')
}

// ── два слага, и они не совпадают ────────────────────────────────────────
// НАСТОЯЩИЙ слаг GitHub меняет каждый пробел на дефис по одному: у
// заголовка «CLI ↔ MCP parity» стрелка выброшена, два пробела подряд дают
// «cli--mcp-parity», и ссылки внутри репозитория написаны именно так
// (`../AGENTS.md#cli--mcp-parity`, `formats.md#parm--parameters`).
// Слаг ВОРОТ 14 (`slugGitHub` в md.mjs — копия функции рельса) схлопывает
// пробелы в один: «cli-mcp-parity». Оба сразу удовлетворить нельзя, и
// расхождение видно только на заголовках, где выброшенный знак стоит
// между пробелами (в снимке таких шесть, ссылок на них две).
// Решение: id заголовка — слаг ВОРОТ (иначе п. 14 краснит слаговую
// строку на EN-страницах), а ЯКОРЬ В ССЫЛКЕ переписывается через таблицу
// «настоящий слаг → id» целевого файла. Ссылка при этом попадает в тот же
// заголовок, а обе строки пункта 14 зелёные. Замер до правки:
// 4 битых якоря (шаг-01-ru).
function slugNastoyashij(t) {
  return String(t).trim().toLowerCase()
    .replace(/[`*_~]/g, '')
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s/g, '-')
}
const YAKORYA_FAJLA = new Map()
function yakorjaFajla(f) {
  if (YAKORYA_FAJLA.has(f)) return YAKORYA_FAJLA.get(f)
  const m = new Map()
  let vKode = false
  for (const l of bezShapki(chit(f)).split('\n')) {
    if (l.startsWith('```')) { vKode = !vKode; continue }
    if (vKode) continue
    const h = l.match(/^(#{1,6})\s+(.*)$/)
    if (!h) continue
    const t = h[2].trim()
    m.set(slugNastoyashij(t), slugGitHub(t))
  }
  YAKORYA_FAJLA.set(f, m)
  return m
}

// ── русский якорь → id заголовка страницы (10.09, раунд 6) ──────────────
// Зеркало раунда 6 пишет якоря ссылок ПО-РУССКИ (`README.md#установка`,
// `../AGENTS.md#соответствие-cli-mcp`, `#как-сервер-mcp`) — так их строит
// сборщик перевода. Карта `yakorjaFajla` собрана по английскому файлу,
// русского слага в ней нет, и ссылка уходила в страницу сырой строкой,
// тогда как id заголовка русской страницы — английский (см. правку
// «Якоря RU-страницы» в stranicaRazdela: там, где деревья совпадают,
// id берётся у EN-заголовка). Замер до правки: 4 битых якоря
// (`/changelog/`, `/commands/` ×2, `/install/`), пункт 14 красный.
//
// Правило: заголовки перевода и источника стоят в одном порядке — это
// ровно то, что сверяет пункт 15 (`derevo`), и у всех 22 файлов совпало.
// Значит, русский заголовок номер i отвечает английскому номер i, а его
// id — `slugGitHub` английского текста. Карта строится только когда числа
// заголовков равны; не равны — прежнее поведение (сырая строка).
//
// Ключи русской карты кириллические, английской — латинские, поэтому
// запасной ход не может изменить ни одной английской ссылки: сначала
// спрашивается карта источника, и только промах ведёт в русскую.
const ZAGOLOVKI_RE = /^(#{1,6})\s+(.*)$/
function zagolovki(md) {
  const out = []
  let vKode = false
  for (const l of String(md).replace(/\r/g, '').split('\n')) {
    if (l.startsWith('```')) { vKode = !vKode; continue }
    if (vKode) continue
    const h = l.match(ZAGOLOVKI_RE)
    if (h) out.push(h[2].trim())
  }
  return out
}
const YAKORYA_RU = new Map()
function yakorjaRu(f) {
  if (YAKORYA_RU.has(f)) return YAKORYA_RU.get(f)
  const m = new Map()
  const per = PEREVODY.get(f)
  if (per) {
    const ru = zagolovki(bezShapki(per.telo))
    const en = zagolovki(bezShapki(chit(f)))
    if (ru.length === en.length) {
      ru.forEach((t, i) => { m.set(slugNastoyashij(t), slugGitHub(en[i])) })
    }
  }
  YAKORYA_RU.set(f, m)
  return m
}

function yakor(f, y) {
  if (!y) return ''
  const m = yakorjaFajla(f)
  if (m.has(y)) return '#' + m.get(y)
  const ru = yakorjaRu(f)
  if (ru.has(y)) return '#' + ru.get(y)
  return '#' + y
}

// Ссылка источника → адрес сайта. Внутренние `.md`, которые лежат на
// сайте, становятся /docs/<раздел>/#<якорь>; всё прочее (код, LICENSE,
// тесты) уходит на GitHub по хешу снимка. Наружу мы не ходим.
function perepisatSsylku(href, fajlIstochnika) {
  if (!href) return href
  if (/^(https?:|mailto:|tel:)/.test(href)) return href
  if (href.startsWith('#')) return yakor(fajlIstochnika, href.slice(1))
  const [put0, yakorRaw] = href.split('#')
  // путь относительно каталога файла-источника, нормализованный
  const bazaKat = dirname(fajlIstochnika)
  const celPuti = relative('.', join(bazaKat === '.' ? '' : bazaKat, put0)).replace(/\\/g, '/')
  const razdel = PO_FAJLU.get(celPuti)
  // якорь на GitHub остаётся настоящим слагом: там страница чужая
  if (razdel) return adres('/docs/' + razdel.klyuch + '/') + yakor(celPuti, yakorRaw)
  return GITHUB + '/blob/' + KOMMIT + '/' + celPuti + (yakorRaw ? '#' + yakorRaw : '')
}

// Правка ссылок внутри блоков (текст остаётся дословным, меняется адрес).
function ssylkiVBlokah(bloki, fajl) {
  const ispr = s => String(s).replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    (_, t, u) => '[' + t + '](' + perepisatSsylku(u, fajl) + ')')
  return bloki.map(b => {
    if (b.t === 'p') return { ...b, tekst: ispr(b.tekst) }
    if (b.t === 'ul' || b.t === 'ol') return { ...b, punkty: b.punkty.map(ispr) }
    if (b.t === 'table') return { ...b, shapka: b.shapka.map(ispr), ryady: b.ryady.map(r => r.map(ispr)) }
    if (/^h[1-4]$/.test(b.t)) return { ...b, tekst: ispr(b.tekst) }
    return b
  })
}

// Блоки одного раздела: файлы склеиваются подряд, H1 первого файла
// становится заголовком страницы, H1 следующих — обычным H2 (иначе текст
// второго файла остался бы без своего имени). Id заголовков — слаг
// GitHub: им живут ссылки самих источников, и его же сверяет пункт 14.
// ── таблица инструментов: две колонки источника → три колонки замка ──────
// Правки 4 и 7 доводки 09.09. Источник (`references/tools.md`, оба языка)
// держит инструменты двухколоночной таблицей `| `имя(аргументы)` | зачем |`.
// Волна печатала её обычной таблицей: на 390 она оставалась таблицей и
// уезжала за правый край, а на 1440 имя переносилось ВНУТРИ слова
// («td_search_operat / ors(query,»), потому что имя и аргументы стояли
// одной ячейкой. Замок d1 разбирает ту же строку на три колонки —
// ИНСТРУМЕНТ / АРГУМЕНТЫ / ДЛЯ ЧЕГО, — имя не переносится (white-space:
// nowrap), а на 390 строка раскладывается списком с метками колонок.
// Разбор тот же, что у фикстуры (site/generator/fixtura.mjs, функция instrumenty):
// имя — `[a-z_]+`, аргументы — всё в скобках. Опознаётся ФОРМОЙ строк,
// а не подписью шапки: по-русски она «Инструмент | Зачем», по-английски
// «Tool | Use it for», и завязываться на них нельзя.
const RE_INSTR = /^`([a-z_][a-z0-9_]*)\((.*)\)`$/
function vInstr(bloki) {
  return bloki.map(b => {
    if (b.t !== 'table' || !b.ryady.length) return b
    if (!b.ryady.every(r => r.length === 2 && RE_INSTR.test(r[0].trim()))) return b
    return {
      ...b,
      t: 'instr',
      ryady: b.ryady.map(r => {
        const m = r[0].trim().match(RE_INSTR)
        return { imya: m[1], argi: m[2], zachem: r[1] }
      }),
    }
  })
}

// EN-файл того снимка, на который сослался перевод: `kommit: <хеш>` шапки
// → fanout/раунд-5/istochnik-<7 знаков>. Нет каталога или файла — null,
// и код берётся из текущего снимка, как раньше.
// На рабочем дереве репозитория каталогов-снимков нет, и тот же файл
// достаётся из истории: `git show <kommit>:<файл>`. Нет ни каталога,
// ни коммита — null, и код берётся из текущего источника, как раньше.
function enMdSnimkaPerevoda(shapkaRU, fajl) {
  const m = String(shapkaRU).match(/kommit:\s*([0-9a-f]{7,40})/)
  if (!m) return null
  const put = join(VOLNA, 'istochnik-' + m[1].slice(0, 7), fajl)
  if (existsSync(put)) return readFileSync(put, 'utf-8').replace(/\r/g, '')
  if (!GIT_DEREVO) return null
  const t = git('show', m[1] + ':' + fajl)
  return t ? t.replace(/\r/g, '') : null
}

function razdelBloki(r) {
  const out = []
  let h1 = null
  r.fajly.forEach((f, i) => {
    const md = chistka(f, bezShapki(chit(f)))
    let b = razbor(md)
    b = ssylkiVBlokah(b, f)
    b = vInstr(b)
    for (const x of b) {
      if (x.t === 'h1') {
        if (i === 0 && h1 === null) { h1 = x.tekst; continue }
        out.push({ ...x, t: 'h2', id: slugGitHub(x.tekst) })
        continue
      }
      if (/^h[2-4]$/.test(x.t)) { out.push({ ...x, id: slugGitHub(x.tekst) }); continue }
      out.push(x)
    }
  })
  return { h1: h1 || r.en, bloki: out }
}

// ── RU-переводы: файл к файлу, по строке `istochnik:` в шапке ────────────
function perevody() {
  const karta = new Map()
  if (!existsSync(RU_DIR)) return karta
  const vse = []
  const obhod = (kat, pref = '') => {
    for (const e of readdirSync(kat, { withFileTypes: true })) {
      if (e.name.startsWith('.')) continue
      if (e.isDirectory()) obhod(join(kat, e.name), pref + e.name + '/')
      else if (e.name.endsWith('.md')) vse.push(pref + e.name)
    }
  }
  obhod(RU_DIR)
  for (const f of vse) {
    const t = readFileSync(join(RU_DIR, f), 'utf-8').replace(/\r/g, '')
    const m = t.match(/istochnik:\s*(\S+)/)
    if (!m) { console.log('  RU ' + f + ': нет строки istochnik: — файл пропущен'); continue }
    karta.set(m[1], { fajl: f, tekst: t, telo: t.replace(/^---[\s\S]*?\n---\n/, '') })
  }
  return karta
}

// Дерево заголовков markdown (уровень + порядок) — как считает рельс.
function derevo(md) {
  const out = []
  let vKode = false
  for (const l of String(md).replace(/\r/g, '').split('\n')) {
    if (l.startsWith('```')) { vKode = !vKode; continue }
    if (vKode) continue
    const m = l.match(/^(#{1,6})\s+(.*)$/)
    if (m) out.push(m[1].length)
  }
  return out.join(',')
}

// ═════════════════════════════════════════════════════════════════════════
//  Инлайновый скрипт языка. Устройство — лендинга (fanout/раунд-5/
//  лендинг-en/otchet.md, «Устройство двуязычия одной страницей»):
//  ?lang=en|ru → localStorage('td-atlas.lang') → navigator.language,
//  выбор стоит в <head>, подстановка зовётся последней строкой <body>.
//  ?lang= память НЕ пишет; пишет только клик человека.
//  Отличие доков от лендинга: кроме строк хрома (data-t) язык меняет ещё
//  и тело страницы — оно живёт в <template id="ru-telo">. Английский
//  вариант при первом переключении не удаляется, а откладывается в
//  переменную: отсоединённый узел в document.querySelectorAll не попадает,
//  и в дереве документа всегда ровно один язык.
// ═════════════════════════════════════════════════════════════════════════
function skriptJazyka(T) {
  return `
<script>
(function () {
  var T = ${JSON.stringify(T)}
  var KEY = 'td-atlas.lang'
  var q = new URLSearchParams(location.search).get('lang')
  var lang = (q === 'en' || q === 'ru') ? q : null
  var otkuda = lang ? '?lang=' : ''
  if (!lang) { try { var z = localStorage.getItem(KEY); if (z === 'en' || z === 'ru') { lang = z; otkuda = 'localStorage' } } catch (e) {} }
  if (!lang) { lang = /^ru/i.test(navigator.language || '') ? 'ru' : 'en'; otkuda = 'navigator.language' }
  var d = document.documentElement
  var gotovo = null, uzlov = 0, otlozhen = null
  function telo(l) {
    /* RU-тело лежит в <template>; EN-тело откладывается, а не копируется */
    var t = document.getElementById('ru-telo')
    if (!t) return
    var m = document.querySelector('main')
    if (!m) return
    var art = m.querySelector('article.proza')
    var asd = m.querySelector('aside.oglavlenie')
    if (l === 'ru') {
      if (otlozhen) return
      var f = t.content.cloneNode(true)
      var na = f.querySelector('article.proza'), ns = f.querySelector('aside.oglavlenie')
      otlozhen = { art: art, asd: asd }
      if (na && art) art.replaceWith(na)
      if (asd) { if (ns) asd.replaceWith(ns); else asd.remove() }
      else if (ns) m.appendChild(ns)
    } else {
      if (!otlozhen) return
      if (art && otlozhen.art) art.replaceWith(otlozhen.art)
      if (asd && otlozhen.asd) asd.replaceWith(otlozhen.asd)
      else if (asd && !otlozhen.asd) asd.remove()
      else if (!asd && otlozhen.asd) m.appendChild(otlozhen.asd)
      otlozhen = null
    }
  }
  function stavit(l) {
    lang = l
    d.lang = l
    d.setAttribute('data-lang', l)
    telo(l)
    var u = document.querySelectorAll('[data-t]')
    for (var i = 0; i < u.length; i++) {
      var el = u[i], v = T[el.getAttribute('data-t')]
      if (!v) continue
      var at = el.getAttribute('data-t-atr')
      if (at) el.setAttribute(at, v[l]); else el.textContent = v[l]
    }
    var kn = document.querySelectorAll('.perekl a[data-lang]')
    for (var j = 0; j < kn.length; j++) kn[j].classList.toggle('tut', kn[j].getAttribute('data-lang') === l)
    uzlov = u.length
    return u.length
  }
  d.lang = lang
  d.setAttribute('data-lang', lang)
  window.__jazyk = {
    T: T, KEY: KEY, otkuda: otkuda,
    get lang() { return lang },
    get uzlov() { return uzlov },
    get gotovo() { return gotovo },
    stavit: stavit,
    /* идемпотентно: зовётся последней строкой <body>, до первого кадра */
    primenit: function () { if (gotovo !== null) return gotovo; stavit(lang); gotovo = performance.now(); return gotovo },
  }
})()
</script>`
}

// Отклик интерфейса: копирование, переключатель, подсветка оглавления.
// Делегирование на документе — иначе кнопки, приехавшие из <template>,
// остались бы немыми. Подтверждение рисуется ВНУТРИ самой кнопки (ловушка 3
// подготовителя: тост в конце <body> мера не увидит), держится 1800 мс.
//
// Доводка 09.09, правка 3: отклик «Скопировать» повторяет CTA лендинга
// (fanout/раунд-5/лендинг-en/site/src/stil.css, «правка 5» и main.ts):
// подпись лежит в span внутри кнопки, новая подпись создаётся НА КЛИКЕ
// и снимается после — держать её в дереве постоянно нельзя (узел с
// computed opacity 0 на живой странице и лишний текст в списке кнопок).
// Галочка — инлайновый SVG, а не символ: check.sh считает «✓» эмодзи.
//
// Доводка 09.09, правка 1: подсветка текущего раздела в оглавлении.
// Меняется ТОЛЬКО класс на <li> (цвет), ни одного смещения и ни одной
// петли — ворота 4-доки держатся. Оглавление и меню лежат под sticky,
// мера параллакса их и так не читает.
const SKRIPT_OTKLIKA = `
<script>
(function () {
  var J = window.__jazyk
  function podpis(k) { return (J && J.T[k]) ? J.T[k][J.lang] : '' }

  /* ── кнопка копирования: подпись переезжает в span ─────────────────── */
  function obernut(b) {
    if (b.querySelector('.pdp-est')) return
    var est = document.createElement('span')
    est.className = 'pdp pdp-est'
    est.textContent = (b.textContent || '').trim()
    var kl = b.getAttribute('data-t')
    b.textContent = ''
    b.appendChild(est)
    /* ключ едет с кнопки на подпись: у кнопки теперь есть дети, и
       подстановка языка в саму кнопку снесла бы всю машинерию */
    if (kl) { est.setAttribute('data-t', kl); b.removeAttribute('data-t') }
  }
  function galka() {
    var NS = 'http://www.w3.org/2000/svg'
    var svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('viewBox', '0 0 12 12')
    svg.setAttribute('aria-hidden', 'true')
    var put = document.createElementNS(NS, 'path')
    put.setAttribute('d', 'M2 6.4 L4.7 8.9 L10 3.1')
    put.setAttribute('fill', 'none')
    put.setAttribute('stroke', 'currentColor')
    put.setAttribute('stroke-width', '1.6')
    put.setAttribute('stroke-linecap', 'round')
    put.setAttribute('stroke-linejoin', 'round')
    svg.appendChild(put)
    return svg
  }
  function otvet(b) {
    clearTimeout(b.__t); clearTimeout(b.__t2)
    var staraja = b.querySelector('.pdp-nov')
    if (staraja) staraja.remove()
    obernut(b)
    var nov = document.createElement('span')
    nov.className = 'pdp pdp-nov'
    nov.appendChild(galka())
    nov.appendChild(document.createTextNode(podpis('kopir.gotovo')))
    b.appendChild(nov)
    /* класс ставится через кадр: без этого переходу не с чего начинать */
    requestAnimationFrame(function () { b.classList.add('gotovo') })
    b.__t = setTimeout(function () {
      b.classList.remove('gotovo')
      b.__t2 = setTimeout(function () {
        var n = b.querySelector('.pdp-nov')
        if (n) n.remove()
      }, 260)
    }, 1800)
  }

  /* ── подсветка текущего раздела в оглавлении ───────────────────────── */
  var celi = null, tek = -1, zhdet = false
  function sobrat() {
    var ogl = document.querySelector('aside.oglavlenie')
    if (!ogl) return null
    var li = ogl.querySelectorAll('li'), out = []
    for (var i = 0; i < li.length; i++) {
      var a = li[i].querySelector('a')
      if (!a) continue
      var id = (a.getAttribute('href') || '').replace(/^#/, '')
      var el = id ? document.getElementById(id) : null
      if (el) out.push({ li: li[i], a: a, el: el })
    }
    return out.length ? out : null
  }
  function schitat() {
    zhdet = false
    if (!celi) return
    /* граница — низ шапки плюс запас: раздел считается текущим, как только
       его заголовок дошёл до верха окна */
    var granica = 130, i = 0
    for (var k = 0; k < celi.length; k++) {
      if (celi[k].el.getBoundingClientRect().top <= granica) i = k
    }
    var d = document.documentElement
    if (window.innerHeight + window.scrollY >= d.scrollHeight - 2) i = celi.length - 1
    if (i === tek) return
    tek = i
    for (var j = 0; j < celi.length; j++) {
      var akt = j === i
      celi[j].li.classList.toggle('akt', akt)
      if (akt) celi[j].a.setAttribute('aria-current', 'true')
      else celi[j].a.removeAttribute('aria-current')
    }
  }
  function pri() { if (zhdet) return; zhdet = true; requestAnimationFrame(schitat) }
  /* ── признак прокрутки у схемы шире экрана (раунд 8, находка L1) ─────────
     Класс ставится по НАСТОЯЩЕЙ ширине полотна, а не по медиазапросу: на
     768 все три схемы сайта влезают целиком (722 из 722), и подпись там
     была бы неправдой. Ни петли, ни привязки к прокрутке здесь нет —
     ворота 4-доки меряют и то, и другое. */
  function shirokieShemy() {
    var s = document.querySelectorAll('figure.shema')
    for (var i = 0; i < s.length; i++) {
      var p = s[i].querySelector('.shema-polotno')
      s[i].classList.toggle('shirokaya', !!p && p.scrollWidth > p.clientWidth + 1)
    }
  }

  function zavesti() {
    var kn = document.querySelectorAll('button.kopir')
    for (var i = 0; i < kn.length; i++) obernut(kn[i])
    shirokieShemy()
    celi = sobrat(); tek = -1; schitat()
  }

  /* ── панель разделов (правка 4 доводки 2, 09.09) ────────────────────────
     Открывает значок в шапке, закрывают крестик, задник, выбор раздела
     и Esc. Ездит только по действию человека: ворота 4-доки меряют
     бесконечные анимации и привязку к прокрутке, здесь ни того, ни другого.
     Обработчик делегирован документу — панель переживает смену языка,
     но пусть переживёт и любую подмену узлов. */
  function panel() { return document.querySelector('nav.menu') }
  function stavitPanel(otkryta) {
    var m = panel(), z = document.querySelector('.zaslon')
    var k = document.querySelector('.knopka-menu')
    if (m) m.classList.toggle('otkryt', otkryta)
    if (z) z.classList.toggle('otkryt', otkryta)
    if (k) k.setAttribute('aria-expanded', otkryta ? 'true' : 'false')
    if (otkryta && m) { var p = m.querySelector('a'); if (p && p.focus) p.focus() }
    else if (!otkryta && k && k.focus && document.activeElement &&
             m && m.contains(document.activeElement)) k.focus()
  }
  addEventListener('keydown', function (e) {
    if (e.key === 'Escape') stavitPanel(false)
  })

  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target : null
    if (t) {
      if (t.closest('.knopka-menu')) {
        e.preventDefault()
        var m0 = panel()
        stavitPanel(!(m0 && m0.classList.contains('otkryt')))
        return
      }
      if (t.closest('.zakryt-panel') || t.closest('.zaslon')) { e.preventDefault(); stavitPanel(false); return }
      if (t.closest('nav.menu a')) stavitPanel(false)
    }
    var a = e.target.closest ? e.target.closest('a[data-lang]') : null
    if (a) {
      e.preventDefault()
      var l = a.getAttribute('data-lang')
      if (J) J.stavit(l)
      try { localStorage.setItem('td-atlas.lang', l) } catch (x) {}
      return
    }
    var b = e.target.closest ? e.target.closest('button.kopir') : null
    if (!b) return
    var kod = b.closest('.kod')
    var pre = kod ? kod.querySelector('pre') : null
    if (!pre) return
    var t = pre.textContent
    var vsyo = function () { otvet(b) }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(vsyo, vsyo)
    } else { vsyo() }
  })

  /* смена языка подменяет и прозу, и оглавление: узлы прежней подсветки
     и прежние кнопки уходят из дерева, заводить надо заново */
  if (J) {
    var st = J.stavit
    J.stavit = function (l) { var r = st(l); zavesti(); return r }
  }
  addEventListener('scroll', pri, { passive: true })
  addEventListener('resize', pri, { passive: true })
  addEventListener('resize', shirokieShemy, { passive: true })
  zavesti()
})()
</script>`

// ═════════════════════════════════════════════════════════════════════════
//  Сборка живой страницы
// ═════════════════════════════════════════════════════════════════════════
const YAKORYA = [
  { imya: HROM['hdr.s2'].en, href: adres('/#s2'), dt: 'hdr.s2' },
  { imya: HROM['hdr.s3'].en, href: adres('/#s3'), dt: 'hdr.s3' },
  { imya: HROM['hdr.s4'].en, href: adres('/#s4'), dt: 'hdr.s4' },
  { imya: HROM['hdr.s5'].en, href: adres('/#s5'), dt: 'hdr.s5' },
]

function shapkaZhivaja() {
  return shapka({
    yakorya: YAKORYA,
    knopkaMenu: KNOPKA_MENU,
    docsImya: HROM['hdr.docs'].en,
    docsDt: 'hdr.docs',
    // href — якорь страницы, а не ?lang=: пункт 14 резолвит строку запроса
    // как ИМЯ ФАЙЛА (`--dir/<раздел>/?lang=en`) и объявил бы такую ссылку
    // битой. Язык меняет обработчик по data-lang, ключ ?lang= остаётся
    // рабочим адресом для рельса и внешних ссылок.
    perekl: '<a href="#verh" data-lang="ru">RU</a><i>·</i><a href="#verh" data-lang="en" class="tut">EN</a>',
    knopkaHref: adres('/#s5'),
    knopkaImya: HROM['hdr.cta'].en,
    knopkaDt: 'hdr.cta',
  })
}

function menuZhivoe(tekushij) {
  return menu(RAZDELY.map(r => ({
    imya: r.en, href: adres('/docs/' + r.klyuch + '/'), akt: r.klyuch === tekushij, dt: 'razdel.' + r.klyuch,
  })), {
    eyebrow: HROM['menu.eyebrow'].en, eyebrowDt: 'menu.eyebrow',
    zakryt: HROM['menu.zakryt'].en, zakrytDt: 'menu.zakryt',
    knopkaZakryt: KNOPKA_ZAKRYT,
    aria: 'sections',
  })
}

// ── правка 4 доводки 2 (09.09): панель разделов ──────────────────────────
// Полоса «Разделы» под шапкой снята; вместо неё значок в шапке и панель,
// выезжающая слева. Подпись кнопки лежит в скрытом от глаза <span>,
// а не в title: приёмка ищет узел по textContent («Разделы»/«Sections»,
// «Закрыть»/«Close»), и кнопка без текста для неё была бы лишней.
// data-t стоит на самом <span>, а не на <button>: подстановка языка пишет
// textContent целиком и снесла бы значок (тот же приём, что у кнопки
// копирования).
const ZNAK_MENU = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" fill="none" ' +
  'stroke="currentColor" stroke-width="1.5" stroke-linecap="round">' +
  '<path d="M2 4.5h12M2 8h12M2 11.5h12"/></svg>'
const ZNAK_KREST = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" fill="none" ' +
  'stroke="currentColor" stroke-width="1.5" stroke-linecap="round">' +
  '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg>'

const KNOPKA_MENU = `<button class="knopka-menu" type="button" aria-controls="razdely" aria-expanded="false">` +
  `${ZNAK_MENU}<span class="sr" data-t="polosa.razdely">${HROM['polosa.razdely'].en}</span></button>`
const KNOPKA_ZAKRYT = `<button class="zakryt-panel" type="button">` +
  `${ZNAK_KREST}<span class="sr" data-t="menu.zakryt">${HROM['menu.zakryt'].en}</span></button>`
const ZASLON = '<div class="zaslon"></div>'

// Плашка русской страницы. ОДНА строка всегда (правка 5 доводки 09.09:
// замок d1 держит «Перевод английской документации от <дата> · English» на
// каждой русской странице, волна её потеряла); отставание и отсутствие
// перевода дописываются в ту же строку, а не заводят вторую.
// Класс .plashka снимается пунктами 7 и 16 приёмки — дата в ней числом
// репозитория не считается; пункт 11 её читает, банлист прогнан.
function plashkaHTML(vid) {
  // Перевода нет вовсе — про дату говорить нечего, строка одна.
  const tekst = vid === 'net' ? HROM['plashka.net'].ru
    : vid === 'otstal' ? HROM['plashka.perevod'].ru + '. ' + HROM['plashka.otstal'].ru
    : HROM['plashka.perevod'].ru
  return `<p class="plashka ru-tolko">${tekst}<i>·</i><a href="#verh" data-lang="en">${HROM['plashka.ssylka'].ru}</a></p>`
}

// Вторая строка — только «Инструменты»: имена и описания там английские.
const PLASHKA_INSTR = `<p class="plashka ru-tolko">${HROM['plashka.instr'].ru}</p>`

const KOMMENTARIJ = (imya, ist) => `  Сайт документации td-atlas, раунд 5, раскладка d1 (LOCK.md, «Раунд 5»).
  Раздел: ${imya}. Источник: ${ist} снимка ${KOMMIT}.
  Собрано site/generator/sborka.mjs — руками не править, правится генератор.
  Тема, шапка, подвал, стили кода и таблиц — из fanout/раунд-5/веер/ (d1).
  В дереве документа ровно один язык; второй лежит в <template id="ru-telo">.`

function stranicaRazdela(r) {
  const en = razdelBloki(r)
  const per = PEREVODY
  // RU-тело: один перевод на каждый файл раздела; нет хотя бы одного —
  // страница остаётся английской с плашкой «этой страницы по-русски нет».
  const kuski = r.fajly.map(f => per.get(f) || null)
  const estPerevod = kuski.every(Boolean)
  let ru = null, otstal = false
  if (estPerevod) {
    let h1ru = null
    const bloki = []
    r.fajly.forEach((f, i) => {
      const enMd = chit(f)
      if (derevo(bezShapki(kuski[i].telo)) !== derevo(bezShapki(enMd))) otstal = true
      const m = kuski[i].tekst.match(/hesh:\s*([0-9a-f]{8,64})/)
      if (!m || !heshFajla(enMd).startsWith(m[1])) otstal = true
      // Код-блоки русской страницы берутся из ТОГО снимка, который назван
      // в шапке перевода (`kommit:`), а не из текущего. Перевод ссылается
      // на код ПОРЯДКОВЫМ номером (`@@kod:N@@`); README 09.09 вставил блок
      // `curl … install.sh` в середину, и все ссылки после него съезжали
      // на один: русская «Установка» показывала curl под абзацем «Сначала
      // поставьте TouchDesigner», `claude mcp add` — под абзацем про
      // td_build, а пятнадцатый блок терялся вовсе (замер 09.09 на
      // istochnik-b311825). Со снимком перевода страница остаётся связной,
      // а о её возрасте говорит плашка «Он отстаёт от английского».
      const kody = razbor(bezShapki(enMdSnimkaPerevoda(kuski[i].tekst, f) || enMd))
        .filter(x => x.t === 'code')
      let b = razbor(chistka(f, bezShapki(kuski[i].telo)), kody)
      b = ssylkiVBlokah(b, f)
      b = vInstr(b)
      for (const x of b) {
        if (x.t === 'h1') {
          if (i === 0 && h1ru === null) { h1ru = x.tekst; continue }
          bloki.push({ ...x, t: 'h2', id: slug(x.tekst) })
          continue
        }
        if (/^h[2-4]$/.test(x.t)) { bloki.push({ ...x, id: slug(x.tekst) }); continue }
        bloki.push(x)
      }
    })
    // Якоря RU-страницы: там, где деревья совпадают, берётся id
    // EN-заголовка — тогда ссылка с якорем GitHub работает в обоих языках.
    const enZag = en.bloki.filter(x => /^h[2-4]$/.test(x.t))
    const ruZag = bloki.filter(x => /^h[2-4]$/.test(x.t))
    if (enZag.length === ruZag.length) ruZag.forEach((x, i) => { x.id = enZag[i].id })
    // Русское имя раздела, а не английское: у README после 10.09 нет
    // markdown-заголовка H1 вовсе (он жил в вырезанной HTML-шапке), и
    // запасное имя стало видимым заголовком страницы «Установка».
    ru = { h1: h1ru || r.ru, bloki }
  }

  // Плашка: строка замка всегда, вторая строка — только «Инструменты».
  const estInstr = en.bloki.some(x => x.t === 'instr')
  const plashka = plashkaHTML(!estPerevod ? 'net' : (otstal ? 'otstal' : null)) +
    (estInstr ? '\n    ' + PLASHKA_INSTR : '')
  const OGL = { eyebrow: HROM['ogl.eyebrow'].en, aria: 'on this page', eyebrowDt: 'ogl.eyebrow', ariaDt: 'ogl.aria' }
  const telo = [
    shapkaZhivaja(),
    ZASLON,
    '<main>',
    '  ' + menuZhivoe(r.klyuch),
    '  ' + prozaHTML({ h1: en.h1, bloki: en.bloki, plashka, knopka: KNOPKA_ZHIVAJA, metki: METKI_INSTR_EN, razdelMetki: '\n', shemy: true, podskazkaShemy: HROM['shema.shirokaya'].en, metkiTablic: true }),
    '  ' + oglavlenie(en.bloki, OGL),
    '</main>',
    ru
      ? '<template id="ru-telo">' +
        prozaHTML({ h1: ru.h1, bloki: ru.bloki, plashka, knopka: KNOPKA_ZHIVAJA, metki: METKI_INSTR_RU, razdelMetki: '\n', shemy: true, podskazkaShemy: HROM['shema.shirokaya'].ru, metkiTablic: true }) +
        oglavlenie(ru.bloki, OGL) +
        '</template>'
      : '',
  ].filter(Boolean).join('\n')

  return stranica({
    kommentarij: KOMMENTARIJ(r.en, r.fajly.join(', ')),
    yaz: 'en',
    titul: `${en.h1} — td-atlas docs`,
    favicon: '../favicon.svg',
    dopCSS: CSS_D1 + CSS_ZHIVOJ,
    klassTela: `d1 str-${r.klyuch}`,
    telo,
    golova: skriptJazyka(HROM),
    hvost: '\n<script>window.__jazyk.primenit()</script>' + SKRIPT_OTKLIKA,
  })
}

// ── страница-указатель /docs/ ────────────────────────────────────────────
// Список разделов — навигация, а не проза: он лежит в <nav>, вне
// article.proza. Так пункт 11 не краснеет на названии раздела «Решения»
// (ПРИЁМКА.md прямо говорит, что это не находка), а пункт 7 не ищет
// источник для слов генератора.
function stranicaUkazatelya() {
  const punkty = RAZDELY.map(r => `<a href="${adres('/docs/' + r.klyuch + '/')}">
        <b data-t="razdel.${r.klyuch}">${esc(r.en)}</b>
        <span>${esc(r.fajly.join(', '))}</span>
      </a>`).join('\n      ')
  const telo = [
    shapkaZhivaja(),
    ZASLON,
    '<main>',
    '  ' + menuZhivoe(null),
    '  <div class="kolonka">',
    `    <article class="proza">
      <h1 id="verh" data-t="ukaz.h1">${esc(HROM['ukaz.h1'].en)}</h1>
      <p data-t="ukaz.abzac">${esc(HROM['ukaz.abzac'].en)}</p>
    </article>`,
    `    <nav class="ukazatel" aria-label="sections">
      <p class="eyebrow" data-t="ukaz.eyebrow">${esc(HROM['ukaz.eyebrow'].en)}</p>
      ${punkty}
    </nav>`,
    '  </div>',
    '</main>',
  ].join('\n')
  return stranica({
    kommentarij: KOMMENTARIJ('указатель', 'разделы зеркала'),
    yaz: 'en',
    titul: 'Documentation — td-atlas',
    favicon: 'favicon.svg',
    // Правого оглавления у указателя нет: на странице нет своих H2.
    // Правило стоит в @media, иначе оно перебило бы одноколоночный 390
    // из CSS_D1 (одна специфичность, но ниже по файлу) и дало бы 409 px
    // прокрутки на телефоне.
    //
    // РАУНД 8, находка 3: колонка была снята с сетки, но ширину за собой
    // не отдавала — `max-width:70ch` на колонке оставлял справа пустое
    // поле шириной с оглавление соседних страниц, и указатель читался как
    // страница с пустой колонкой «на странице». Ограничение снято: меру
    // строки держит `.proza p` (CSS_D1, 70ch), а список разделов занимает
    // всю оставшуюся ширину и на широком экране встаёт в два столбца —
    // 21 пункт в один столбец давал страницу вдвое выше окна.
    // `auto-fill` с порогом 400 px, а не 320: на 1440 полоса содержимого
    // 1064 px, и порог 320 давал три столбца по 323 px, в которых
    // plugin/skills/touchdesigner/references/tools.md рвался посреди слова
    // (та же беда, что находка 2, только заведённая своими руками).
    // При 400 px на 1440 столбцов два по 508 px — длинные имена файлов
    // встают одной строкой; на 861–1000 px столбец по-прежнему один.
    dopCSS: CSS_D1 + CSS_ZHIVOJ + `
@media (min-width:861px){ main{grid-template-columns:236px minmax(0,1fr)} }
.kolonka{min-width:0}
@media (min-width:861px){
  .ukazatel{display:grid; grid-template-columns:repeat(auto-fill,minmax(400px,1fr));
    column-gap:48px}
  .ukazatel .eyebrow{grid-column:1/-1}
}
`,
    klassTela: 'd1 str-ukazatel',
    telo,
    golova: skriptJazyka(HROM),
    hvost: '\n<script>window.__jazyk.primenit()</script>' + SKRIPT_OTKLIKA,
  })
}

// ═════════════════════════════════════════════════════════════════════════
//  Запись
// ═════════════════════════════════════════════════════════════════════════
function pisat(put, html) {
  mkdirSync(dirname(put), { recursive: true })
  writeFileSync(put, html)
  return html.length
}

let PEREVODY = new Map()

function sobratDoki() {
  PEREVODY = perevody()
  const vyhod = join(SAJT, 'dist', 'docs')
  rmSync(vyhod, { recursive: true, force: true })
  mkdirSync(vyhod, { recursive: true })
  // Тот же файл, что раздаёт лендинг: vite кладёт site/public в корень dist,
  // а страницы доков ссылаются на ../favicon.svg рядом с собой.
  copyFileSync(join(SAJT, 'public', 'favicon.svg'), join(vyhod, 'favicon.svg'))
  let vsego = 0
  const n = pisat(join(vyhod, 'index.html'), stranicaUkazatelya())
  console.log('  /docs/'.padEnd(24) + (n / 1024).toFixed(0) + ' КиБ')
  vsego += n
  for (const r of RAZDELY) {
    const html = stranicaRazdela(r)
    const b = pisat(join(vyhod, r.klyuch, 'index.html'), html)
    const per = r.fajly.every(f => PEREVODY.has(f)) ? 'RU есть' : 'RU нет'
    console.log(('  /docs/' + r.klyuch + '/').padEnd(24) + (b / 1024).toFixed(0) + ' КиБ  ' + per)
    vsego += b
  }
  console.log('  страниц ' + (RAZDELY.length + 1) + ', всего ' + (vsego / 1024).toFixed(0) + ' КиБ, переводов ' + PEREVODY.size)
}

function sobratFiksturu(kat) {
  const d = resolve(kat)
  mkdirSync(d, { recursive: true })
  for (const { fajl, html } of fikstura()) {
    writeFileSync(join(d, fajl), html)
    console.log('  фикстура ' + fajl.padEnd(22) + html.length + ' б')
  }
}

// Сверка фикстуры с веером: тот же байт, кроме комментария в шапке
// и переводов строки МЕЖДУ ТЕГАМИ. Второе появилось 09.09: между
// </td> и <td> (и между </li> и <li>) генератор ставит перевод строки,
// чтобы textContent не склеивал соседние ячейки в одно «число»
// (пункт 7). В раскладке этот пробельный узел не рисуется — сверка
// фикстуры с кадрами замка осталась 0 %, — но байт в байт с веером
// расходится, и сверять надо после нормализации межтеговых пробелов.
function sverit() {
  const bezKomm = s => s.replace(/<!--[\s\S]*?-->\n/, '').replace(/>\s+</g, '><')
  let plohih = 0
  for (const { fajl, html } of fikstura()) {
    const put = join(VOLNA, 'веер', 'd1', fajl)
    const veer = readFileSync(put, 'utf-8')
    const ok = bezKomm(html) === bezKomm(veer)
    if (!ok) plohih++
    console.log('  сверка ' + fajl.padEnd(22) + (ok ? 'байт в байт' : 'РАСХОЖДЕНИЕ ' +
      (bezKomm(html).length - bezKomm(veer).length) + ' б'))
    if (!ok) {
      const a = bezKomm(veer), b = bezKomm(html)
      let i = 0
      while (i < a.length && a[i] === b[i]) i++
      console.log('    веер:  …' + JSON.stringify(a.slice(Math.max(0, i - 60), i + 90)))
      console.log('    доки:  …' + JSON.stringify(b.slice(Math.max(0, i - 60), i + 90)))
    }
  }
  if (plohih) process.exit(1)
}

// ═════════════════════════════════════════════════════════════════════════
if (FIX_DIR) {
  sobratFiksturu(FIX_DIR)
} else if (flag('sverit-veer')) {
  sverit()
} else if (flag('tolko-doki')) {
  sobratDoki()
} else {
  // Полная сборка: сначала лендинг (vite чистит dist), потом доки внутрь.
  console.log('лендинг (vite):')
  execFileSync('npm', ['--prefix', SAJT, 'run', 'build:sajt'], { stdio: 'inherit' })
  console.log('доки:')
  sobratDoki()
}
