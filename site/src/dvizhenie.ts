// Прокрутка, появление по прокрутке и параллакс. Модуль ГРУЗИТСЯ ОТДЕЛЬНО
// и лениво: gsap + ScrollTrigger + lenis весят 185 КБ, и на браузере рельса
// их разбор держал главный поток от 0.4 до 2.4 с — ровно в окне развёртки
// первого экрана (замер proba/razvertka.mjs). Развёртка теперь на CSS
// и идёт на компоновщике, а этот модуль подхватывается после первой
// отрисовки, когда его блокировка уже никому не мешает.
import Lenis from 'lenis'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import type { Zhivoj } from './objekt/pole'

gsap.registerPlugin(ScrollTrigger)

const VYKAT = 'expo.out'
const q = <T extends Element>(s: string) => [...document.querySelectorAll<T>(s)]

// ── появление по прокрутке, одноразовое, триггер 85 % ────────────────────
const BLOKI = '#s2 .obj, #s3 .obj, #s4 .obj, #s5 .terminal, #s5 .prav'
const STROKI = '#s2 .kicker, #s2 h2 span, #s2 p.abz, #s3 .kicker, #s3 h2 span, #s3 .niz p.abz,'
  + ' #s4 .kicker, #s4 h2 span, #s4 p.abz, #s5 .kicker, #s5 h2 span'
const PODPISI = '#s3 .strelka, #s4 .strelka, .nomer'

function poyavlenie() {
  const gruppa = (sel: string, ot: gsap.TweenVars, dlit: number, stagger: number) => {
    for (const sek of q('.ekran')) {
      const uzly = [...sek.querySelectorAll(sel)].filter(n => sek.contains(n))
      if (!uzly.length) continue
      gsap.fromTo(uzly, { opacity: 0, ...ot }, {
        opacity: 1, y: 0, duration: dlit, ease: VYKAT, stagger,
        scrollTrigger: { trigger: sek, start: 'top 85%', once: true },
      })
    }
  }
  gruppa(BLOKI, { y: 160 }, 0.8, 0.5)
  gruppa(STROKI, { y: 44 }, 0.8, 0.12)
  gruppa(PODPISI, { y: 22 }, 0.7, 0.06)
}

// ── параллакс: объект 0.12, задник 0.20, текст 0.02 ──────────────────────
// Наклон считается от верха своей секции: y = k · (scrollY − верх секции),
// то есть слой отстаёт от страницы ровно на k своего пути в любой точке.
// Пишется в CSS-свойство translate, а не в transform: transform на этих же
// узлах держит появление, и два хозяина одного свойства дрались бы.
const SLOI: Array<[string, number]> = [
  ['#s2 .zadnik, #s3 .zadnik, #s4 .zadnik, #s5 .zadnik', 0.2],
  ['#s2 .obj, #s3 .obj, #s4 .obj', 0.12],
  ['#s2 .tekst, #s3 .tekst, #s4 .tekst, #s5 .tekst', 0.02],
  // Терминал s5 лежит ВНУТРИ .tekst, наклоны складываются: 0.02 + 0.10 = 0.12.
  ['#s5 .terminal', 0.1],
]

function parallaks() {
  type Sloj = { el: HTMLElement; k: number; verh: number }
  let sloi: Sloj[] = []
  const sobrat = () => {
    sloi = []
    for (const [sel, k] of SLOI) {
      for (const el of q<HTMLElement>(sel)) {
        const sek = el.closest('.ekran') as HTMLElement | null
        if (!sek) continue
        sloi.push({ el, k, verh: sek.offsetTop })
      }
    }
  }
  sobrat()
  // Слои дальних секций не трогаются: запись translate — это стиль и слой
  // на каждый кадр, а на программном растеризаторе рельса кадр и так
  // впритык. Окно ±1.2 экрана: слой входит в кадр уже поставленным.
  const polozhit = (y: number) => {
    const zapas = window.innerHeight * 1.2
    for (const s of sloi) {
      if (Math.abs(s.verh - y) > zapas + window.innerHeight) continue
      s.el.style.translate = '0px ' + (s.k * (y - s.verh)).toFixed(2) + 'px'
    }
  }
  ScrollTrigger.create({
    trigger: document.body,
    start: 'top top',
    end: 'bottom bottom',
    scrub: true,
    onRefresh: sobrat,
    onUpdate: self => polozhit(self.scroll()),
  })
  polozhit(window.scrollY)
}

export function pusk(zhiv: Zhivoj | null): Lenis {
  const lenis = new Lenis({ duration: 1, smoothWheel: true })
  lenis.on('scroll', ScrollTrigger.update)
  gsap.ticker.add((t: number) => lenis.raf(t * 1000))
  gsap.ticker.lagSmoothing(0)

  // ДОВОДКА, правка 1. Здесь стояла глушилка волны на время прокрутки
  // (zhiv.edet), и это она давала владельцу «чуть проскролил вниз —
  // анимация останавливается». Снята: волна встаёт только когда первый
  // экран целиком вне окна, и решает это IntersectionObserver в pole.ts.
  // Цена — доля тяжёлых кадров рельса, но она поправкой 07.09 переведена
  // в справку, а слово владельца про живой hero — ворота.
  void zhiv

  poyavlenie()
  parallaks()
  return lenis
}
