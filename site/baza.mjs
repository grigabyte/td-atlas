// Базовый путь сайта — ОДНО место на всю сборку.
//
// GitHub Pages раздаёт этот репозиторий по адресу
// https://grigabyte.github.io/td-atlas/, поэтому наружу сайт уезжает
// не в корень домена, а под префикс. Префикс задаётся переменной
// окружения BASE (умолчание «/»), и отсюда его читают ОБЕ половины
// сборки: `vite.config.ts` (лендинг и ассеты) и `generator/sborka.mjs`
// (страницы доков). Двух независимых мест для одного значения нет —
// разошлись бы молча.
//
//   npm --prefix site run build                  → корень, адреса «/docs/…»
//   BASE=/td-atlas/ npm --prefix site run build  → «/td-atlas/docs/…»
//
// Значение нормализуется: ведущий и замыкающий слэш обязательны,
// повторные слэши схлопываются. «/» — корень, `adres()` тогда
// возвращает свой аргумент без изменений (байт в байт прежняя сборка).

function normalizovat(v) {
  let b = String(v == null ? '' : v).trim()
  if (b === '') b = '/'
  if (!b.startsWith('/')) b = '/' + b
  if (!b.endsWith('/')) b += '/'
  return b.replace(/\/{2,}/g, '/')
}

export const BAZA = normalizovat(process.env.BASE)

// Внутренний абсолютный адрес под базой: '/docs/install/' → '/td-atlas/docs/install/',
// '/#s5' → '/td-atlas/#s5'. Аргумент обязан начинаться со слэша.
export function adres(put) {
  const p = String(put)
  if (!p.startsWith('/')) throw new Error('adres(): адрес обязан начинаться со слэша — ' + p)
  return BAZA + p.slice(1)
}
