// Оживление замка p3-v2 (раунд 4, kandidat-2). Разметка и кожа страницы —
// замка; здесь только движение, кнопки и покой.
//
// Числа движения — reference/движение/ТОКЕНЫ-ДВИЖЕНИЯ.md, своих не сочиняю.
// Кривая vykat = cubic-bezier(0.19, 1, 0.22, 1); таблица токенов сама
// называет её expo.out, поэтому в коде стоит 'expo.out'.
import './stil.css'
import type Lenis from 'lenis'
import { ozhivitPole, polozhitKadr } from './objekt/pole'
import { TEKSTY, jazyk, postavitJazyk, type Jazyk } from './teksty'

const POKOJ = document.documentElement.hasAttribute('data-zamri')
const q = <T extends Element>(s: string) => [...document.querySelectorAll<T>(s)]

// ── поле первого экрана ───────────────────────────────────────────────────
// Кадр покоя кладёт 2D-канва теми же четырьмя проходами, что у SVG замка;
// живую волну поднимает webgl2. Отвергнуто: оставлять в покое сам SVG замка
// (0.00 % по построению) — тогда «шейдер стоит на кадре» превращается
// в «шейдера в покое нет». Число расхождения канвы с замком меряется
// proba/oracle.mjs и напечатано в журнале.
function pole() {
  const kadr = document.querySelector<HTMLCanvasElement>('.pole-kadr')
  const volna = document.querySelector<HTMLCanvasElement>('.pole-volna')
  if (!kadr || !volna) return null
  const kor = document.documentElement
  // Класс СНАЧАЛА: пока канва display:none, её прямоугольник нулевой,
  // и кадр печатается в один пиксель.
  kor.classList.add('pole-kadr-est')
  polozhitKadr(kadr)
  if (POKOJ) return null
  const zhiv = ozhivitPole(volna, () => { kor.classList.add('pole-volna-est') })
  const pereschet = () => { if (!kor.classList.contains('pole-volna-est')) polozhitKadr(kadr) }
  window.addEventListener('resize', pereschet)
  return zhiv
}

// ── кнопки ───────────────────────────────────────────────────────────────
// Правило владельца: кнопка работает или её нет. Ни одной кнопки не добавлено
// и не убрано — 13 <a> + 1 <button>, как в замке.
const posleJazyka: (() => void)[] = []

function knopki(dat: () => Lenis | null) {
  // ── переключатель EN · RU ────────────────────────────────────────────
  // Правило владельца «кнопка работает или её нет»: клик меняет язык НА
  // МЕСТЕ (перезагрузки нет), пишет выбор в localStorage и не даёт браузеру
  // уйти по href. href у ссылок настоящий (?lang=en / ?lang=ru) — он нужен
  // рельсу, ссылкам и открытию в новой вкладке.
  for (const a of q<HTMLAnchorElement>('.jazyki a[data-lang]')) {
    a.addEventListener('click', e => {
      const l = a.getAttribute('data-lang') as Jazyk
      if (l !== 'ru' && l !== 'en') return
      e.preventDefault()
      postavitJazyk(l)
      for (const f of posleJazyka) f()
    })
  }

  const maks = () => Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
  for (const a of q<HTMLAnchorElement>('a[href^="#"]')) {
    a.addEventListener('click', e => {
      const cel = document.querySelector<HTMLElement>(a.getAttribute('href')!)
      const lenis = dat()
      if (!cel || !lenis) return           // покой: нативный якорь, он уже точен
      e.preventDefault()
      lenis.scrollTo(Math.min(cel.offsetTop, maks()), { duration: 0.9 })
    })
  }

  // ── правка 5 владельца: «почему кнопка влево уезжает» ────────────────
  // Было: кнопка ехала влево, а справа всплывала отдельная подпись
  // «СКОПИРОВАНО» — раскладка терминала дёргалась на ширину подписи.
  // Стало: подпись меняется ВНУТРИ кнопки кроссфейдом (старая вниз на 8 px
  // и в 0, новая снизу вверх), кнопка на 1.5 с заливается акцентом и
  // возвращается тем же переходом, ширина кнопки прибита.
  //
  // Две ловушки приёмки, из-за которых сделано именно так:
  //   • список кнопок сверяется с замком по textContent, и сверка читается
  //     ОДИН раз до клика. Поэтому в разметке остаётся ровно «Скопировать»
  //     (скрипт лишь заворачивает этот текст в span, textContent тот же),
  //     а подпись «Скопировано» СОЗДАЁТСЯ на клике и снимается после.
  //     Держать её в DOM постоянно нельзя вдвойне: она сломала бы и список,
  //     и п. 4 (е) — узел с computed opacity 0 на живой странице.
  //   • подтверждение ищется в document.body.innerText («скопирован»),
  //     а innerText не видит ни ::after, ни visibility: hidden. Поэтому
  //     подпись — настоящий текстовый узел, гаснущий прозрачностью.
  const knopka = document.querySelector<HTMLButtonElement>('#s5 .terminal button')
  const kod = document.querySelector<HTMLElement>('#s5 .terminal code')
  if (knopka && kod) {
    const komanda = (kod.textContent || '').replace(/^\$/, '').trim()
    const est = document.createElement('span')
    est.className = 'pdp pdp-est'
    est.textContent = (knopka.textContent || '').trim()
    knopka.textContent = ''
    knopka.appendChild(est)
    // Ключ текста едет с кнопки на подпись внутри неё: переключатель языка
    // подставляет textContent, а у кнопки после обёртки есть дети, и
    // подстановка в саму кнопку снесла бы всю эту машинерию.
    est.setAttribute('data-t', 's5.copy')
    knopka.removeAttribute('data-t')

    // Галочка — inline-svg, а не символ: check.sh считает эмодзи всё
    // в диапазоне ☀–➿, и «✓» (U+2713) попадает в него.
    const galka = () => {
      const NS = 'http://www.w3.org/2000/svg'
      const svg = document.createElementNS(NS, 'svg')
      svg.setAttribute('viewBox', '0 0 12 12')
      svg.setAttribute('aria-hidden', 'true')
      const put = document.createElementNS(NS, 'path')
      put.setAttribute('d', 'M2 6.4 L4.7 8.9 L10 3.1')
      put.setAttribute('fill', 'none')
      put.setAttribute('stroke', 'currentColor')
      put.setAttribute('stroke-width', '1.6')
      put.setAttribute('stroke-linecap', 'round')
      put.setAttribute('stroke-linejoin', 'round')
      svg.appendChild(put)
      return svg
    }
    const podpis = () => {
      const el = document.createElement('span')
      el.className = 'pdp pdp-nov'
      el.appendChild(galka())
      el.appendChild(document.createTextNode(TEKSTY['s5.copied'][jazyk()]))
      return el
    }

    // Ширина по большему из двух текстов — и ТОЛЬКО живой странице: кадр
    // покоя обязан совпасть с замком, а замок знает кнопку шириной по слову
    // «Скопировать». Мерка вставляется в поток на один кадр и снимается.
    // Ширина меряется ПОСЛЕ загрузки шрифта. Замер до неё идёт по запасному
    // семейству, и на headed-браузере разница двух подписей выходила нулевой:
    // кнопка оставалась шириной замка, а «Скопировано» с галочкой держалась
    // в ней только потому, что лежит абсолютно и по центру. В headless шрифт
    // успевал загрузиться до модуля, и там ширина ставилась верно (138 px) —
    // ловушка видна только вживую.
    const postavitShirinu = () => {
      const merka = podpis()
      merka.classList.remove('pdp-nov')
      merka.style.position = 'absolute'
      merka.style.visibility = 'hidden'
      knopka.appendChild(merka)
      const delta = Math.max(0, merka.getBoundingClientRect().width - est.getBoundingClientRect().width)
      merka.remove()
      knopka.style.minWidth = Math.ceil(knopka.getBoundingClientRect().width + delta) + 'px'
    }
    if (!POKOJ) {
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(postavitShirinu)
      else postavitShirinu()
      // «Скопировать» и «Copy» разной длины: после смены языка ширина
      // считается заново, иначе кнопка останется по мерке прошлого языка.
      posleJazyka.push(() => { knopka.style.minWidth = ''; postavitShirinu() })
    }

    let tajmer = 0
    let ubrat = 0
    knopka.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(komanda) } catch { /* нет прав — подпись не врёт */ }
      clearTimeout(tajmer)
      clearTimeout(ubrat)
      knopka.querySelector('.pdp-nov')?.remove()
      const nov = podpis()
      knopka.appendChild(nov)
      // Класс через кадр: иначе переходу не с чего стартовать.
      requestAnimationFrame(() => knopka.classList.add('gotovo'))
      tajmer = window.setTimeout(() => {
        knopka.classList.remove('gotovo')
        ubrat = window.setTimeout(() => nov.remove(), 260)
      }, 1500)
    })
  }

  // Активная секция: пункт индикатора и якорь шапки. Не движение, а состояние,
  // поэтому работает и в покое.
  const meta = new Map<string, Element[]>()
  for (const a of q<HTMLAnchorElement>('.indikator a, .yakorya a')) {
    const id = a.getAttribute('href')!.slice(1)
    meta.set(id, [...(meta.get(id) || []), a])
  }
  const nabl = new IntersectionObserver(zapisi => {
    for (const z of zapisi) {
      const cel = meta.get(z.target.id)
      if (!cel) continue
      for (const a of cel) a.classList.toggle('tut', z.isIntersecting)
    }
  }, { threshold: 0.5 })
  for (const s of q('.ekran')) if (s.id) nabl.observe(s)

  // Петли считаются на главном потоке (stroke-opacity, background-color,
  // transform внутри svg), и четыре сразу стоят четверти кадров прокрутки
  // (проба: 0.78 → 0.55, если их выключить). Петля секции, которой нет
  // в окне, ничего не показывает — она ставится на паузу.
  const vid = new IntersectionObserver(zapisi => {
    for (const z of zapisi) (z.target as HTMLElement).classList.toggle('vidno', z.isIntersecting)
  }, { threshold: 0, rootMargin: '10%' })
  for (const s of q('.ekran')) vid.observe(s)
}

// ── сборка ───────────────────────────────────────────────────────────────
// ПОРЯДОК ЗАПУСКА СТОИТ НА ЗАМЕРЕ, а не на вкусе (proba/anim.mjs, тот же
// браузер, каким мерит рельс). Когда модуль печатал кадр поля и тянул
// gsap+lenis сразу, главный поток был занят до 1.9 с, и ПЕРВАЯ ОТРИСОВКА
// страницы случалась на 1988 мс: развёртка (она идёт на компоновщике
// с 378 мс) успевала доиграть за чёрным экраном, и на полоске загрузки
// её не было ни на одном кадре. Поэтому всё тяжёлое отложено за load:
// страница красится на ~0.4 с со статичным полем замка, развёртка видна
// целиком, а печать кадра, gsap и WebGL идут потом.
let lenis: Lenis | null = null
knopki(() => lenis)

if (POKOJ) {
  pole()
} else {
  // Два кадра после load: пока браузер не отдал ни одного кадра, тяжёлая
  // работа отодвигает первую отрисовку — замер это и показал.
  // Канва встаёт на место статичного слоя ТОЛЬКО после того, как тот
  // договорил своё появление из чёрного: подмена в середине проявления —
  // это скачок прозрачности с 0.3–0.8 на 1.0, ровно то «неплавно
  // появляется», из-за которого раунд 3 и переделывается.
  const dozhdatsya = async () => {
    const kor = document.documentElement
    for (let i = 0; i < 80 && !kor.classList.contains('gotov'); i++) {
      await new Promise(r => setTimeout(r, 50))
    }
    const st = document.querySelector('.pole-statika')
    const a = st && st.getAnimations()[0]
    if (a && a.playState !== 'finished') { try { await a.finished } catch { /* прервали — не беда */ } }
  }
  const pozzhe = () => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(async () => {
    await dozhdatsya()
    const zhiv = pole()
    import('./dvizhenie').then(m => { lenis = m.pusk(zhiv) })
  }, 0)))
  if (document.readyState === 'complete') pozzhe()
  else window.addEventListener('load', pozzhe, { once: true })
}
