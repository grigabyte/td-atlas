// Фикстура пункта 2: три канонические страницы замка d1, собранные ТЕМ ЖЕ
// шаблоном (docs/shablon.mjs), что и живой сайт, но на ломтиках старого
// снимка fanout/раунд-5/istochnik/ и на RU-текстах веера
// fanout/раунд-5/веер/tekst/ — иначе кадры замка не с чем сравнивать
// (README рендерится как есть, и живая «Установка» с кадром замка
// структурно не совпадает: решение владельца 08.09).
//
// Содержимое — копия fanout/раунд-5/веер/kontent.mjs; изменены только пути
// к снимку и текстам (они лежат в чужом каталоге) и вызовы шаблона под его
// новую подпись. Готовый HTML веера в фикстуру НЕ копируется: тогда ноль
// ничего бы не доказывал.
//
// ФАЙЛЫ ФИКСТУРЫ ОДНОЯЗЫЧНЫ И БЕЗ JS — ловушка 2 подготовителя: кадры
// снимаются локалью Playwright en-US, и скрипт выбора языка внутри
// фикстуры отрендерил бы RU-кадры по-английски (~90 % на правильной
// фикстуре).
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'
import { razbor, kodyIz, slug } from './md.mjs'
import { CSS_D1, stranica, shapka, menu, oglavlenie, prozaHTML, KNOPKA_VEER } from './shablon.mjs'

const TUT = dirname(fileURLToPath(import.meta.url))

// ПЕРЕЕЗД 11.09. Снимок замка (`fanout/раунд-5/istochnik/`) и черновые
// RU-тексты веера лежат в рабочей папке лендинга, а не в репозитории:
// фикстура — мера прошлого раунда, её ломтики привязаны к номерам строк
// того снимка. Каталог снимка называется ключом `--istochnik`, тексты
// веера берутся рядом с ним: `<снимок>/../веер/tekst`.
//
// Всё содержимое фикстуры считается ЛЕНИВО: обычная сборка сайта
// импортирует этот модуль, а снимка под рукой не имеет, и чтение на
// верхнем уровне роняло бы сборку.
let KESH = null
function podgotovit() {
  if (KESH) return KESH
  const i = process.argv.indexOf('--istochnik')
  const IST = i === -1 ? join(TUT, '..', '..') : resolve(process.argv[i + 1])
  const TEKST = join(IST, '..', 'веер', 'tekst')
  const chit = f => readFileSync(join(IST, f), 'utf-8').replace(/\r/g, '')

// ── ломтик по номерам строк с проверкой якоря ────────────────────────────
function lomtik(tekst, ot, do_, yakor) {
  const s = tekst.split('\n')
  const got = s[ot - 1]
  if (got !== yakor) throw new Error(`строка ${ot} источника ожидалась «${yakor}», а лежит «${got}»`)
  return s.slice(ot - 1, do_).join('\n')
}

const README = chit('README.md')
const CLI = chit('cli.md')
const TOOLS = chit('tools.md')

const EN_USTANOVKA = [
  lomtik(README, 258, 258, '## Install'),
  '',
  lomtik(README, 269, 317, 'TouchDesigner has to be installed first: the index is built from *your* copy of'),
  '',
  lomtik(README, 319, 339, '## Compatibility'),
].join('\n')

const EN_CLI = lomtik(CLI, 1, 125, '# The command line')

const KODY_USTANOVKA = kodyIz(EN_USTANOVKA)
const KODY_CLI = kodyIz(EN_CLI)

const GRUPPY_RU = {
  'Index — offline, no running TouchDesigner': 'Индекс: офлайн, без запущенного TouchDesigner',
  'Live — acting on a running instance': 'Живой проект: действия в запущенной программе',
  'Project files — on disk': 'Файлы проекта: на диске',
}

function instrumenty() {
  const gruppy = []
  const stroki = TOOLS.split('\n')
  let tek = null
  for (const l of stroki) {
    const mh = l.match(/^## (.+)$/)
    if (mh) {
      const ru = GRUPPY_RU[mh[1].trim()]
      tek = ru ? { imya: ru, en: mh[1].trim(), ryady: [] } : null
      if (tek) gruppy.push(tek)
      continue
    }
    if (!tek || !l.startsWith('|')) continue
    const c = l.replace(/^\|/, '').replace(/\|\s*$/, '').split('|').map(x => x.trim())
    if (c.length !== 2) continue
    if (/^:?-{2,}:?$/.test(c[0])) continue
    if (c[0] === 'Tool') continue
    const m = c[0].match(/^`([a-z_]+)\((.*)\)`$/)
    if (!m) throw new Error('не разобрал имя инструмента: ' + c[0])
    tek.ryady.push({ imya: m[1], argi: m[2], zachem: c[1] })
  }
  const vsego = gruppy.reduce((s, g) => s + g.ryady.length, 0)
  if (vsego !== 41) throw new Error('инструментов ' + vsego + ', ожидалось 41')
  return gruppy
}

const INSTRUMENTY = instrumenty()
const CHISLO_INSTRUMENTOV = INSTRUMENTY.reduce((s, g) => s + g.ryady.length, 0)

const ru = f => readFileSync(join(TEKST, f), 'utf-8').replace(/\r/g, '')

const STRANICY = {
  ustanovka: {
    fajl: 'ustanovka.html', yaz: 'ru', h1: 'Установка', imya: 'Установка',
    enHref: 'ustanovka.en.html',
    bloki: razbor(ru('ustanovka.ru.md'), KODY_USTANOVKA),
  },
  'ustanovka.en': {
    fajl: 'ustanovka.en.html', yaz: 'en', h1: 'Install', imya: 'Install',
    ruHref: 'ustanovka.html',
    bloki: razbor(EN_USTANOVKA, []),
  },
  instrumenty: {
    fajl: 'instrumenty.html', yaz: 'ru', h1: 'Инструменты', imya: 'Инструменты',
    enHref: '/docs/en/instrumenty/',
    bloki: [],
  },
  komandy: {
    fajl: 'komandy.html', yaz: 'ru', h1: 'Командная строка', imya: 'Команды',
    enHref: '/docs/en/komandy/',
    bloki: razbor(ru('komandy.ru.md'), KODY_CLI),
  },
}

STRANICY.instrumenty.bloki = [
  {
    t: 'p', shag: null,
    tekst: `${CHISLO_INSTRUMENTOV} инструмент MCP в трёх группах. Список привязан к коду тестом \`tests/test_skill_reference.py\`: каждое имя, список параметров и само это число сверяются с сервером. Имена и описания оставлены как в источнике, по-английски.`,
  },
  ...INSTRUMENTY.flatMap(g => [
    { t: 'h2', shag: null, tekst: g.imya, id: slug(g.imya) },
    { t: 'instr', shag: null, ryady: g.ryady },
  ]),
]

const RAZDELY = [
  { imya: 'Установка', klyuch: 'ustanovka' },
  { imya: 'Команды', klyuch: 'komandy' },
  { imya: 'Инструменты', klyuch: 'instrumenty' },
  { imya: 'Ловушки без ошибок', href: '/docs/ru/lovushki/' },
  { imya: 'Скилл для агента', href: '/docs/ru/skill/' },
  { imya: 'Устройство', href: '/docs/ru/ustrojstvo/' },
  { imya: 'Формат .toe/.tox', href: '/docs/ru/format/' },
  { imya: 'Решения', href: '/docs/ru/resheniya/' },
  { imya: 'Публикация', href: '/docs/ru/publikaciya/' },
  { imya: 'Для контрибьюторов', href: '/docs/ru/kontributoram/' },
  { imya: 'Что изменилось', href: '/docs/ru/izmeneniya/' },
]

const YAKORYA_LENDINGA = [
  { imya: 'Находит ошибки', href: '/#s2' },
  { imya: 'Правит сеть', href: '/#s3' },
  { imya: 'Смотрит картинку', href: '/#s4' },
  { imya: 'Установка', href: '/#s5' },
]

const PLASHKA = { tekst: 'Перевод английской документации от 07.09', ssylka: 'English' }

const KOMMENTARIJ = `  Фикстура пункта 2 раунда 5: канонические страницы замка d1, собранные
  генератором доков (docs/sborka.mjs --fixtura) на ломтиках снимка
  fanout/раунд-5/istochnik/ и текстах веера fanout/раунд-5/веер/tekst/.
  Тот же шаблон и тот же CSS, что у живого сайта; языка и JS здесь нет
  нарочно — кадры снимаются локалью en-US.
  Руками не править: правится генератор.`

// ── одна страница фикстуры ────────────────────────────────────────────────
function stranicaFikstury(str) {
  const s = STRANICY[str]
  const enHref = s.yaz === 'en' ? s.ruHref : (s.enHref || '/docs/en/' + str + '/')
  const perekl = s.yaz === 'en'
    ? `<a href="${enHref}">RU</a><i>·</i><b>EN</b>`
    : `<b>RU</b><i>·</i><a href="${enHref}">EN</a>`
  const punkty = RAZDELY.map(r => {
    const svoj = r.klyuch && STRANICY[r.klyuch]
    return {
      imya: r.imya,
      href: svoj ? STRANICY[r.klyuch].fajl : r.href,
      akt: r.klyuch === str.replace(/\.en$/, ''),
    }
  })
  const plashka = s.yaz === 'ru'
    ? `<p class="plashka">${PLASHKA.tekst}<i>·</i><a href="${s.enHref || '/docs/en/'}">${PLASHKA.ssylka}</a></p>`
    : ''
  const telo = `${shapka({
    yakorya: YAKORYA_LENDINGA,
    docsImya: 'Документация',
    perekl,
    knopkaHref: '/#s5',
    knopkaImya: 'Попробовать',
  })}
<div class="polosa"><a class="knopka" href="#razdely">Разделы</a></div>
<main>
  ${menu(punkty)}
  ${prozaHTML({ h1: s.h1, bloki: s.bloki, plashka, knopka: KNOPKA_VEER })}
  ${oglavlenie(s.bloki)}
</main>`
  return stranica({
    kommentarij: KOMMENTARIJ,
    yaz: s.yaz,
    titul: s.yaz === 'en' ? `${s.h1} — td-atlas docs` : `${s.h1} — документация td-atlas`,
    favicon: '../../../../лого/favicon.svg',
    dopCSS: CSS_D1,
    klassTela: `d1 str-${str.replace(/\./g, '-')}`,
    telo,
  })
}

  KESH = { STRANICY, stranicaFikstury }
  return KESH
}

export function fikstura() {
  const { STRANICY, stranicaFikstury } = podgotovit()
  return ['ustanovka', 'ustanovka.en', 'instrumenty', 'komandy']
    .map(str => ({ fajl: STRANICY[str].fajl, html: stranicaFikstury(str) }))
}
