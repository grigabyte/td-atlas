// Крошечный markdown → блоки. Копия fanout/раунд-5/веер/md.mjs (раунд 5)
// с тремя ДОБАВЛЕНИЯМИ для полного зеркала репозитория; всё, что уже
// разбиралось в веере, разбирается байт в байт так же (проверено:
// фикстура п. 2 совпадает с fanout/раунд-5/веер/d1/*.html побайтно):
//   1. YAML-шапка `---` в начале файла (её носит SKILL.md) — снимается;
//   2. нумерованные списки `1. ` → блок `ol` (README, SKILL, AGENTS);
//   3. экранированная черта `\|` внутри ячейки таблицы (docs/cli.md, одна
//      строка) — не режет ячейку.
// Ни одно из трёх в текстах фикстуры не встречается (проверено grep’ом).
//
// Ниже — исходный заголовок веера.
//
// Крошечный markdown → блоки для веера доков (раунд 5).
//
// Зачем свой, а не библиотека: тексты источника рендерятся ДОСЛОВНО, а
// разметка должна быть нашей (кнопка «Скопировать» у каждого кода, якорь `#`
// у заголовка, три колонки таблицы инструментов). Библиотека подсветки
// запрещена контрактом, а markdown-библиотека притащила бы свои классы и
// свой HTML вокруг кода.
//
// Понимает ровно то, что есть в источнике: ## / ### заголовки, абзацы,
// ``` блоки с языком, таблицы GFM, списки `- `, и в строке — `код`,
// **жирное**, *курсив*, [текст](адрес). Больше ничего не встречается.
//
// Маркеры веера:
//   @@kod:N@@                 — сюда подставляется N-й код-блок EN-источника
//                               (гарантия «код-блоки RU = EN дословно»)
//   <!-- шаг 03 -->            — начало шага d3; имя шага берётся из
//                               следующего за маркером ## заголовка,
//                               <!-- шаг - --> закрывает шаги

const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z',
  и: 'i', й: 'j', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
  с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch',
  ъ: '', ы: 'y', ь: '', э: 'e', ю: 'ju', я: 'ja',
}

export function slug(s) {
  const t = String(s).toLowerCase().replace(/`|\*/g, '')
  let out = ''
  for (const ch of t) out += TRANSLIT[ch] ?? ch
  return out.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48)
}

// Слаг GitHub — им живут ссылки вида `docs/cli.md#install` в самих
// источниках, и ворота 14 сверяют id заголовков EN-страницы именно с ним
// (tools/priemka-doki.mjs, функция slugGitHub — здесь её копия дословно).
// Нижний регистр, пунктуация выброшена, пробелы → дефис, юникод сохранён.
// Транслитерация веера (`slug` выше) остаётся только у фикстуры.
export function slugGitHub(s) {
  return String(s).trim().toLowerCase()
    .replace(/[`*_~]/g, '')
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
}

export const esc = s => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// Внутристрочное. Порядок: сначала `код` (внутри него ничего не разбирается),
// потом ссылки, потом жирное и курсив, потом ~~зачёркнутое~~.
export function vstroke(s) {
  const kuski = []
  let t = String(s).replace(/`([^`]+)`/g, (_, k) => {
    kuski.push('<code>' + esc(k) + '</code>')
    return '\u0001' + (kuski.length - 1) + '\u0001'
  })
  t = esc(t)
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, txt, url) => `<a href="${url}">${txt}</a>`)
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  t = t.replace(/~~([^~]+)~~/g, '<del>$1</del>')
  t = t.replace(/\u0001(\d+)\u0001/g, (_, i) => kuski[Number(i)])
  return t
}

// YAML-шапка в начале файла (`---` … `---`). Её носит SKILL.md; на сайт
// она не идёт — это метаданные скилла, не текст документации.
export function bezShapki(md) {
  const t = String(md).replace(/\r/g, '')
  if (!t.startsWith('---\n')) return t
  const konec = t.indexOf('\n---\n', 3)
  return konec === -1 ? t : t.slice(konec + 5)
}

// Ячейки строки таблицы. `\|` внутри ячейки — литеральная черта, а не
// граница колонки (docs/cli.md, ключ `--family TOP\|CHOP\|…`).
function jachejki(l) {
  return l.replace(/^\|/, '').replace(/\|\s*$/, '')
    .split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'))
}

// Разбор в блоки. kody — массив строк кода EN-источника для @@kod:N@@.
export function razbor(md, kody = []) {
  const stroki = String(md).replace(/\r/g, '').split('\n')
  const bloki = []
  let shag = null
  let i = 0
  const abzac = []
  const sbros = () => {
    if (!abzac.length) return
    bloki.push({ t: 'p', shag, tekst: abzac.join(' ') })
    abzac.length = 0
  }
  while (i < stroki.length) {
    const l = stroki[i]
    const msh = l.match(/^<!--\s*шаг\s+(\d+|-)\s*-->$/)
    if (msh) { sbros(); shag = msh[1] === '-' ? null : { nomer: msh[1] }; i++; continue }
    if (/^<!--/.test(l)) {           // комментарий, в том числе многострочный
      sbros()
      while (i < stroki.length && !stroki[i].includes('-->')) i++
      i++; continue
    }
    if (!l.trim()) { sbros(); i++; continue }
    const mk = l.match(/^@@kod:(\d+)@@$/)
    if (mk) {
      sbros()
      const k = kody[Number(mk[1]) - 1]
      if (k === undefined) throw new Error('нет код-блока ' + mk[1] + ' в источнике')
      bloki.push({ t: 'code', shag, yaz: k.yaz, tekst: k.tekst, nomer: Number(mk[1]) })
      i++; continue
    }
    if (l.startsWith('```')) {
      sbros()
      const yaz = l.slice(3).trim()
      const telo = []
      i++
      while (i < stroki.length && !stroki[i].startsWith('```')) { telo.push(stroki[i]); i++ }
      i++
      bloki.push({ t: 'code', shag, yaz, tekst: telo.join('\n') })
      continue
    }
    const mh = l.match(/^(#{1,4})\s+(.*)$/)
    if (mh) {
      sbros()
      bloki.push({ t: 'h' + mh[1].length, shag, tekst: mh[2].trim(), id: slug(mh[2]) })
      i++; continue
    }
    if (/^\|/.test(l)) {
      sbros()
      const ryady = []
      while (i < stroki.length && /^\|/.test(stroki[i])) {
        // Строка таблицы, перенесённая в источнике на следующую физическую
        // строку. Markdown требует, чтобы ряд кончался чертой; в снимке
        // `references/tools.md` ряд td_variant_restore обёрнут по ширине
        // (строки 60–61), и без склейки razbor обрывал таблицу на нём,
        // а последний инструмент (td_variant_diff) оставался «таблицей
        // из одной шапки» — 40 строк вместо 41. Замер 09.09.
        let l = stroki[i]
        i++
        while (!/\|\s*$/.test(l) && i < stroki.length && stroki[i].trim() && !/^\|/.test(stroki[i])) {
          l += ' ' + stroki[i].trim()
          i++
        }
        ryady.push(jachejki(l))
      }
      const shapka = ryady.shift()
      if (ryady.length && ryady[0].every(c => /^:?-{2,}:?$/.test(c))) ryady.shift()
      bloki.push({ t: 'table', shag, shapka, ryady })
      continue
    }
    if (/^\d+\.\s/.test(l)) {
      sbros()
      const punkty = []
      while (i < stroki.length && (/^\d+\.\s/.test(stroki[i]) || /^\s{2,}\S/.test(stroki[i]))) {
        if (/^\d+\.\s/.test(stroki[i])) punkty.push(stroki[i].replace(/^\d+\.\s+/, ''))
        else punkty[punkty.length - 1] += ' ' + stroki[i].trim()
        i++
      }
      bloki.push({ t: 'ol', shag, punkty })
      continue
    }
    if (/^[-*]\s/.test(l)) {
      sbros()
      const punkty = []
      while (i < stroki.length && (/^[-*]\s/.test(stroki[i]) || /^\s{2,}\S/.test(stroki[i]))) {
        if (/^[-*]\s/.test(stroki[i])) punkty.push(stroki[i].replace(/^[-*]\s+/, ''))
        else punkty[punkty.length - 1] += ' ' + stroki[i].trim()
        i++
      }
      bloki.push({ t: 'ul', shag, punkty })
      continue
    }
    abzac.push(l.trim())
    i++
  }
  sbros()
  return bloki
}

// Код-блоки из markdown: только они, в порядке документа.
export function kodyIz(md) {
  const out = []
  const stroki = String(md).replace(/\r/g, '').split('\n')
  for (let i = 0; i < stroki.length; i++) {
    if (!stroki[i].startsWith('```')) continue
    const yaz = stroki[i].slice(3).trim()
    const telo = []
    i++
    while (i < stroki.length && !stroki[i].startsWith('```')) { telo.push(stroki[i]); i++ }
    out.push({ yaz, tekst: telo.join('\n') })
  }
  return out
}
