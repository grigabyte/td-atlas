// Тексты страницы, оба языка. Единственный источник строк — teksty.json
// рядом (собран proba/sobrat-teksty.mjs из fanout/раунд-5/en-teksty/ru.json
// и B.json плюс правки-дырки контракта). Почему JSON, а не литерал прямо
// здесь: тот же набор обязан попасть в ИНЛАЙНОВЫЙ скрипт <head> — модуль
// отложен и опаздывает за первый кадр, — а инлайновый скрипт не умеет
// import. Один файл данных читают оба: сборщик страницы (proba/pravka.mjs)
// и этот модуль. Двух копий строк в проекте нет.
import nabor from './teksty.json'

export type Jazyk = 'ru' | 'en'
export type Nabor = Record<string, { ru: string; en: string }>

export const TEKSTY = nabor as Nabor
export const KLYUCH_JAZYKA = 'td-atlas.lang'

// Ворота языка живут в инлайновом скрипте <head> (см. index.html): он
// выбрал язык и подставил тексты до первого кадра. Модуль им пользуется,
// а своей копии выбора не держит — иначе два хозяина у одного числа.
type Vorota = {
  T: Nabor
  KEY: string
  otkuda: string
  lang: Jazyk
  uzlov: number
  gotovo: number | null
  stavit(l: Jazyk): number
  primenit(): number
}
declare global {
  interface Window { __jazyk?: Vorota }
}

export function jazyk(): Jazyk {
  const v = window.__jazyk
  if (v) return v.lang
  return document.documentElement.lang === 'ru' ? 'ru' : 'en'
}

export function tekst(klyuch: string): string {
  const v = TEKSTY[klyuch]
  return v ? v[jazyk()] : ''
}

// Клик по переключателю: язык на месте + запись выбора. ?lang= в память
// не пишется (это рельс и ссылки), пишет только явный выбор человека.
export function postavitJazyk(l: Jazyk): number {
  const v = window.__jazyk
  const n = v ? v.stavit(l) : 0
  try { localStorage.setItem(KLYUCH_JAZYKA, l) } catch { /* приватное окно: страница работает, выбор не запомнится */ }
  return n
}
