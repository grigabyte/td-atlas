// Объект первого экрана — поле-волна на canvas/WebGL2, фрагментный шейдер,
// без библиотек. ИСТОЧНИК, как велит контракт раунда 4:
// fanout/раунд-3/доводка-hero/site/src/objekty/pole-shader.ts (устройство,
// маска-текстура, вал, разгон) — переупаковано под новый DOM.
//
// Что изменено против источника и почему:
//   • ГЕОМЕТРИЯ И ЦВЕТА — замка раунда 4, а не раунда 3: 44 полилинии из
//     fanout/раунд-4/p3-v2/chasti/pole.svgfrag (x от −60 до 1488, не обрезаны)
//     и четыре прохода расщепления замка (#ff2a2a −4,2 / #2a7fff 4,−2 /
//     #2aff2a 1,4 при 0.30/0.30/0.16 и ведущий #fffdf9 при 0.72). У раунда 3
//     проходы были акцентными: там был другой замок.
//   • «30 FPS» (слово владельца): волна не рисуется, пока страница едет, и
//     канва снимается с показа, когда первый экран уходит из окна. Шаг кадра
//     тот же, 66 мс: волна медленная, ей хватает пятнадцати кадров.
//
// ДОВОДКА 07.09 (правки владельца 1 и 2, КОНТРАКТ-доводка.md):
//   • правка 1. Владелец на своём GPU: «кандидат-2 лагает в 30fps и работает
//     только если находишься на этом экране». Обе причины были здесь:
//     жёсткий шаг 66 мс (15 кадров/с) и отказ рисовать, пока идёт прокрутка
//     (флаг edet). Снято и то и другое. Шаг кадра теперь по замеру, как
//     у kandidat-1, но дельта берётся ПО КАЖДОМУ вызову rAF, а не по
//     нарисованным кадрам: у kandidat-1 шаг был храповиком вниз (нарисовал
//     раз в 33 мс — дельта 33 — шаг остался 33 навсегда). Пауза одна:
//     первый экран ЦЕЛИКОМ вне окна (IntersectionObserver, порог 0,
//     rootMargin 0). Время волны копится только по нарисованным кадрам —
//     возобновление без скачка фазы.
//   • правка 1, разрешение. Числа вала живут в CSS-пикселях, а не в пикселях
//     канвы: добавлен u_dpr, и gl_FragCoord делится на него. Так DPR меняется
//     одной константой и не меняет ни форму волны, ни её скорость.
//   • правка 2. Имена операторов и их засечки едут с гребнем: смещение
//     считается ТОЙ ЖЕ функцией волны, что в шейдере (volna()), тем же
//     временем и той же амплитудой, каждый нарисованный кадр.
//   • «НЕПЛАВНО ПОЯВЛЯЕТСЯ»: канва входит за 600 мс из чёрного (токен
//     ПЛАН-раунд-4, «Движение»), а не возникает сразу.
//   • «ОБРЕЗАН ПО СТОРОНАМ»: посадка та же, что у SVG замка —
//     viewBox 0 0 1440 900, preserveAspectRatio xMidYMid slice, канва во весь
//     экран; своей копии геометрии для узкого окна нет.
import { POLE } from './pole-dannye'

const VB_W = 1440
const VB_H = 900
const posadka = (w: number, h: number) => {
  const s = Math.max(w / VB_W, h / VB_H)
  return { s, ox: (w - VB_W * s) / 2, oy: (h - VB_H * s) / 2 }
}

// Четыре прохода — дословно из замка (p3-v2.html, <g stroke-linecap="round">).
const PROHODY = [
  { dx: -4, dy: 2, cvet: '#ff2a2a', rgb: [1.0, 0.1647, 0.1647], alfa: 0.3 },
  { dx: 4, dy: -2, cvet: '#2a7fff', rgb: [0.1647, 0.498, 1.0], alfa: 0.3 },
  { dx: 1, dy: 4, cvet: '#2aff2a', rgb: [0.1647, 1.0, 0.1647], alfa: 0.16 },
  { dx: 0, dy: 0, cvet: '#fffdf9', rgb: [1.0, 0.9922, 0.9765], alfa: 0.72 },
] as const

const KANVA = '#101010'

type Tochka = [number, number]
let razobrano: Tochka[][] | null = null
function tochki(): Tochka[][] {
  if (razobrano) return razobrano
  razobrano = POLE.map((l) => {
    const out: Tochka[] = []
    const re = /(-?[\d.]+) (-?[\d.]+)/g
    let m: RegExpExecArray | null
    while ((m = re.exec(l.d))) out.push([Number(m[1]), Number(m[2])])
    return out
  })
  return razobrano
}

/** Маска поля: белые штрихи с альфой линии, 1 к 1 с окном. Это содержимое
 *  ОДНОГО прохода до его непрозрачности — как в SVG альфа стоит на пути,
 *  а непрозрачность прохода на группе. */
function ispech(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const g = c.getContext('2d')!
  const { s, ox, oy } = posadka(w, h)
  g.lineWidth = s
  g.lineCap = 'round'
  g.strokeStyle = '#ffffff'
  const pts = tochki()
  POLE.forEach((l, i) => {
    g.globalAlpha = l.so
    g.beginPath()
    pts[i].forEach(([x, y], k) => {
      const X = x * s + ox
      const Y = y * s + oy
      if (k === 0) g.moveTo(X, Y)
      else g.lineTo(X, Y)
    })
    g.stroke()
  })
  return c
}

/** Конечный кадр без волны на 2D-канве: покой, запас без WebGL2 и первые
 *  PAUZA_MS живой страницы.
 *
 *  СЛОЯМИ, а не штрихами. В замке непрозрачность прохода стоит на группе
 *  (<use opacity="0.30">), то есть проход сначала рисуется целиком, потом
 *  накладывается. Штрихи с alfa·so на общей канве дают другое число там, где
 *  линии одного прохода пересекаются, и кадр расходился с замком на 4.50 %
 *  при пороге 5 % (proba/oracle.mjs). Четыре промежуточные канвы стоят
 *  однократных ~50 мс — цена, которую источник раунда 3 не мог себе позволить
 *  каждый кадр, а однократный кадр покоя может.
 */
function slozhitPlosko(cel: HTMLCanvasElement) {
  const w = cel.width
  const h = cel.height
  const g = cel.getContext('2d')!
  const { s, ox, oy } = posadka(w, h)
  g.globalAlpha = 1
  g.fillStyle = KANVA
  g.fillRect(0, 0, w, h)
  const pts = tochki()
  const sloj = document.createElement('canvas')
  sloj.width = w
  sloj.height = h
  const sg = sloj.getContext('2d')!
  for (const p of PROHODY) {
    sg.clearRect(0, 0, w, h)
    sg.lineWidth = s
    sg.lineCap = 'round'
    sg.strokeStyle = p.cvet
    POLE.forEach((l, i) => {
      sg.globalAlpha = l.so
      sg.beginPath()
      pts[i].forEach(([x, y], k) => {
        const X = (x + p.dx) * s + ox
        const Y = (y + p.dy) * s + oy
        if (k === 0) sg.moveTo(X, Y)
        else sg.lineTo(X, Y)
      })
      sg.stroke()
    })
    g.globalAlpha = p.alfa
    g.drawImage(sloj, 0, 0)
  }
  g.globalAlpha = 1
}

const VERT = `#version 300 es
in vec2 mesto;
void main(){ gl_Position = vec4(mesto, 0.0, 1.0); }`

// ВАЛ (крупная форма). Числа объекта, не хореографии: таблица токенов о них
// молчит, поэтому они объявлены строкой в журнале. Взяты из источника
// раунда 3; изменены два, и оба — по контракту раунда 4:
//   amp2 24 → 34 и skorost2 60 → 150. У источника узор двигался ≈ 21 px/с,
//   а контракт просит «сдвиг узора 40–80 px/с». Скорость поднята у МЕДЛЕННОГО
//   поперечного вала, а не у ряби: гребень всё равно идёт кадр за 6.8 с —
//   форма остаётся крупной, как велит «крупная форма, не рябь».
//   Расчёт сдвига: amp2·ω2 + amp1·ω1 + ampR·ωR = 34·0.924 + 6·1.517 + 3·1.016
//   ≈ 44 px/с.
const VAL = {
  lam1: 1180, naklon1: 0.42, skorost1: 285, amp1: 6,
  temn1: 0.92, zhar1: 0.95, shir1: 2.1, jar1: 2.2,
  lam2: 1020, naklon2: -0.85, skorost2: 150, amp2: 34, dolya2: 0.18,
  ampR: 3, lamR: 340, skorostR: 55,
} as const

const ugl = (skorost: number, lam: number) => ((2 * Math.PI * skorost) / lam).toFixed(5)
const ch = (x: number) => x.toFixed(4)
const v3 = (c: readonly number[]) => `vec3(${ch(c[0])}, ${ch(c[1])}, ${ch(c[2])})`

const FRAG = `#version 300 es
precision highp float;
uniform sampler2D u_pole;
uniform vec2 u_res;
uniform vec2 u_sm;
uniform float u_t;
uniform float u_amp;
uniform float u_dpr;
out vec4 cvet;

const vec3 C0 = ${v3(PROHODY[0].rgb)};
const vec3 C1 = ${v3(PROHODY[1].rgb)};
const vec3 C2 = ${v3(PROHODY[2].rgb)};
const vec3 C3 = ${v3(PROHODY[3].rgb)};
const float A0 = ${ch(PROHODY[0].alfa)}, A1a = ${ch(PROHODY[1].alfa)}, A2a = ${ch(PROHODY[2].alfa)}, A3a = ${ch(PROHODY[3].alfa)};
const vec3 KANVA = vec3(0.0627451);
const float PI2 = 6.2831853;

const float L1 = ${ch(VAL.lam1)}, N1 = ${ch(VAL.naklon1)}, W1 = ${ugl(VAL.skorost1, VAL.lam1)}, A1 = ${ch(VAL.amp1)};
const float L2 = ${ch(VAL.lam2)}, N2 = ${ch(VAL.naklon2)}, W2 = ${ugl(VAL.skorost2, VAL.lam2)}, A2 = ${ch(VAL.amp2)};
const float LR = ${ch(VAL.lamR)}, WR = ${ugl(VAL.skorostR, VAL.lamR)}, AR = ${ch(VAL.ampR)};
const float TEMN = ${ch(VAL.temn1)}, ZHAR = ${ch(VAL.zhar1)}, SHIR = ${ch(VAL.shir1 - 1)}, JAR = ${ch(VAL.jar1)}, D2 = ${ch(VAL.dolya2)};

float pole(vec2 p){ return texture(u_pole, p / u_res).a; }

void main(){
  // pd — пиксели канвы (в них живёт маска), p — CSS-пиксели (в них живут
  // числа вала и в них же считает свою копию волны скрипт подписей).
  vec2 pd = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y);
  vec2 p = pd / u_dpr;

  float f1 = PI2 * (p.x + N1 * p.y) / L1 - W1 * u_t;
  float f2 = PI2 * (p.y + N2 * p.x) / L2 - W2 * u_t;
  float fr = PI2 * (p.x * 0.55 + p.y) / LR - WR * u_t;
  float s1 = sin(f1), s2 = sin(f2), sr = sin(fr);

  vec2 w = u_amp * vec2(
    A1 * 0.34 * cos(f1) + AR * 0.50 * cos(fr),
    A1 * s1 + A2 * s2 + AR * sr
  );
  vec2 wd = w * u_dpr;

  float val = (1.0 - D2) * s1 + D2 * s2;
  float temn = clamp(1.0 + u_amp * TEMN * min(val, 0.0), 0.0, 1.0);
  float zhar = u_amp * ZHAR * max(val, 0.0);
  float shir = 1.0 + u_amp * SHIR * val;
  float jar = clamp(1.0 + u_amp * JAR * val, 0.0, 3.4);
  vec3 c3 = mix(C3, vec3(1.0), zhar * 0.35);

  // Расщепляющие проходы едут на 1.18 и 0.82 от смещения ведущего — как
  // в источнике. Была гипотеза, что из-за этого «линия под подписью» на
  // впадине вала уезжает от подписи (там temn гасит белый ведущий проход,
  // и самой яркой становится красная копия на 1.18·w): расхождение 7.5 px
  // у renderTOP. Проверено сведением всех четырёх проходов к одному w —
  // ЧИСЛО НЕ ИЗМЕНИЛОСЬ (7.5 / 5.5 / 1.0 против 7.5 / 7.0 / 0). Дело было
  // в самой мере: она сверяла профиль шейдера с профилем 2D-канвы покоя,
  // а это разные отрисовщики. Дифференциальная мера (живой кадр против
  // живого) даёт с расщеплением 0–1.5 px у пяти подписей из шести — ровно
  // столько же, сколько без него. Правка откачена: трогать вид hero,
  // о котором владелец сказал «в остальном вроде ок», не за что.
  vec3 c = KANVA;
  c = mix(c, C0, pole(pd - vec2(-4.0,  2.0) * u_sm * shir + wd * 1.18) * A0 * jar);
  c = mix(c, C1, pole(pd - vec2( 4.0, -2.0) * u_sm * shir + wd * 0.82) * A1a * jar);
  c = mix(c, C2, pole(pd - vec2( 1.0,  4.0) * u_sm * shir + wd * 1.00) * A2a * jar);
  c = mix(c, c3, pole(pd + wd) * A3a * temn);
  cvet = vec4(c, 1.0);
}`

function shejder(gl: WebGL2RenderingContext, tip: number, src: string) {
  const s = gl.createShader(tip)!
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('шейдер не собрался')
  return s
}


export type Zhivoj = { stop: () => void }

// ── ВОЛНА В СКРИПТЕ: та же функция, что в шейдере ────────────────────────
// Правка 2 владельца: «названия операторов висят в воздухе и никак
// не прикреплены». Чтобы подпись ехала с гребнем, её смещение обязано
// считаться ТОЙ ЖЕ формулой и теми же uniforms времени, что и картинка.
// Ниже — копия блока `w` из FRAG, буква в букву, в CSS-пикселях.
const W1 = (2 * Math.PI * VAL.skorost1) / VAL.lam1
const W2 = (2 * Math.PI * VAL.skorost2) / VAL.lam2
const WR = (2 * Math.PI * VAL.skorostR) / VAL.lamR

/** Смещение поля в точке (x, y) CSS-пикселей на времени t секунд. */
export function volna(x: number, y: number, t: number, amp: number): [number, number] {
  const f1 = (2 * Math.PI * (x + VAL.naklon1 * y)) / VAL.lam1 - W1 * t
  const f2 = (2 * Math.PI * (y + VAL.naklon2 * x)) / VAL.lam2 - W2 * t
  const fr = (2 * Math.PI * (x * 0.55 + y)) / VAL.lamR - WR * t
  return [
    amp * (VAL.amp1 * 0.34 * Math.cos(f1) + VAL.ampR * 0.5 * Math.cos(fr)),
    amp * (VAL.amp1 * Math.sin(f1) + VAL.amp2 * Math.sin(f2) + VAL.ampR * Math.sin(fr)),
  ]
}

// Шейдер читает маску в точке p + w(p): линия, которая в покое лежит
// в точке q, на кадре видна там, где p + w(p) = q. Подпись обязана встать
// в ту же p, значит решается p = q − w(p). |dw/dy| ≈ 0.28, поэтому простая
// итерация сходится: три шага дают ошибку ≈ 0.28³·43 ≈ 1 px. Разложение
// первого порядка (p ≈ q − w(q)) давало бы до 12 px — мимо ворот 6 px.
const SHAGOV_OBRATA = 3
function obratno(qx: number, qy: number, t: number, amp: number): [number, number] {
  let px = qx
  let py = qy
  for (let i = 0; i < SHAGOV_OBRATA; i++) {
    const [wx, wy] = volna(px, py, t, amp)
    px = qx - wx
    py = qy - wy
  }
  return [px - qx, py - qy]
}

/** Подписи операторов и их засечки: 6 пар. Якорь пары — ДАЛЬНИЙ конец
 *  засечки, тот, что лежит на линии поля; подпись едет с ним как одно
 *  целое, поэтому засечка не отрывается от текста. */
type Podpis = { uzly: SVGElement[]; vx: number; vy: number }
function sobratPodpisi(): Podpis[] {
  const svg = document.querySelector<SVGSVGElement>('.pole-imena')
  if (!svg) return []
  const zas = [...svg.querySelectorAll<SVGPathElement>('.zasechki path')]
  const ime = [...svg.querySelectorAll<SVGTextElement>('.imena text')]
  const out: Podpis[] = []
  for (let i = 0; i < zas.length; i++) {
    const d = zas[i].getAttribute('d') || ''
    const t = [...d.matchAll(/(-?[\d.]+)[ ,](-?[\d.]+)/g)]
    if (!t.length) continue
    const konec = t[t.length - 1]
    const uzly: SVGElement[] = [zas[i]]
    if (ime[i]) uzly.push(ime[i])
    out.push({ uzly, vx: Number(konec[1]), vy: Number(konec[2]) })
  }
  return out
}

/** Поставить подписи на кадр времени t. Смещение считается в CSS-пикселях
 *  и переводится в единицы viewBox (svg посажен той же posadka, что канва). */
function vezti(podpisi: Podpis[], w: number, h: number, t: number, amp: number) {
  const { s, ox, oy } = posadka(w, h)
  for (const p of podpisi) {
    const [dx, dy] = obratno(p.vx * s + ox, p.vy * s + oy, t, amp)
    const tr = 'translate(' + (dx / s).toFixed(2) + 'px, ' + (dy / s).toFixed(2) + 'px)'
    for (const u of p.uzly) u.style.transform = tr
  }
}

// Пауза перед первым кадром волны. Разворачивание контекста WebGL и сборка
// программы на swiftshader держат главный поток 0.6–0.9 с (замер
// proba/razvertka.mjs), и эта блокировка обязана лечь ПОСЛЕ развёртки:
// объект всё это время стоит своим конечным кадром на 2D-канве.
const PAUZA_MS = 900
const RAZGON_MS = 900
// Разрешение канвы. Правка 1 контракта: «канва в разрешении окна, DPR ≤ 1.5».
// Числа вала живут в CSS-пикселях (u_dpr в шейдере), поэтому эта константа
// меняет только резкость и цену кадра, но не форму и не скорость волны.
const DPR_POTOLOK = 1.5
const DPR = () => Math.min(window.devicePixelRatio || 1, DPR_POTOLOK)

/** Конечный кадр на 2D-канве, 1 к 1 с окном. */
export function polozhitKadr(cel: HTMLCanvasElement) {
  const r = cel.getBoundingClientRect()
  const w = Math.max(1, Math.round(r.width))
  const h = Math.max(1, Math.round(r.height))
  if (cel.width !== w || cel.height !== h) { cel.width = w; cel.height = h }
  slozhitPlosko(cel)
}

/** Оживить канву волной. Канва обязана быть ОТДЕЛЬНОЙ от той, на которой
 *  лежит конечный кадр: контексты 2d и webgl2 на одной канве не уживаются. */
export function ozhivitPole(cel: HTMLCanvasElement, gotova: () => void): Zhivoj {
  let ostanovlen = false
  let ramka = 0
  let vidno = true
  let gl: WebGL2RenderingContext | null = null
  const podpisi = sobratPodpisi()

  const razmer = () => {
    const r = cel.getBoundingClientRect()
    const d = DPR()
    const w = Math.max(1, Math.round(r.width * d))
    const h = Math.max(1, Math.round(r.height * d))
    if (cel.width !== w || cel.height !== h) { cel.width = w; cel.height = h; return true }
    return false
  }

  const pusk = () => {
    if (ostanovlen) return
    razmer()
    try {
      gl = cel.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: false })
    } catch { gl = null }
    if (!gl) return
    let prog: WebGLProgram
    try {
      prog = gl.createProgram()!
      gl.attachShader(prog, shejder(gl, gl.VERTEX_SHADER, VERT))
      gl.attachShader(prog, shejder(gl, gl.FRAGMENT_SHADER, FRAG))
      gl.linkProgram(prog)
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error('программа не слинковалась')
    } catch { return }

    gl.useProgram(prog)
    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const mesto = gl.getAttribLocation(prog, 'mesto')
    gl.enableVertexAttribArray(mesto)
    gl.vertexAttribPointer(mesto, 2, gl.FLOAT, false, 0, 0)

    const tex = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)

    const uRes = gl.getUniformLocation(prog, 'u_res')
    const uSm = gl.getUniformLocation(prog, 'u_sm')
    const uT = gl.getUniformLocation(prog, 'u_t')
    const uAmp = gl.getUniformLocation(prog, 'u_amp')
    const uDpr = gl.getUniformLocation(prog, 'u_dpr')
    gl.uniform1i(gl.getUniformLocation(prog, 'u_pole'), 0)

    const perepech = () => {
      const maska = ispech(cel.width, cel.height)
      gl!.bindTexture(gl!.TEXTURE_2D, tex)
      gl!.texImage2D(gl!.TEXTURE_2D, 0, gl!.RGBA, gl!.RGBA, gl!.UNSIGNED_BYTE, maska)
      gl!.viewport(0, 0, cel.width, cel.height)
      gl!.uniform2f(uRes, cel.width, cel.height)
      const ps = posadka(cel.width, cel.height)
      gl!.uniform2f(uSm, ps.s, ps.s)
      gl!.uniform1f(uDpr, DPR())
    }
    perepech()

    // ШАГ КАДРА ПО ЗАМЕРУ, а не константой (правка 1). Дельта берётся
    // по КАЖДОМУ вызову rAF: если считать её по нарисованным кадрам, шаг
    // становится храповиком вниз — нарисовал раз в 33 мс, дельта 33,
    // среднее 33, шаг остался 33 навсегда (так у kandidat-1).
    // Пороги и инерция среднего — по замеру proba/fps.mjs на этой машине.
    // У kandidat-1 было alfa 0.15 и порог 22 мс: при честных 60 Гц (дельта
    // 16.7) двух-трёх подряд задержанных кадров хватало, чтобы среднее
    // перевалило 22 и шаг ушёл на 33 — волна болталась между 60 и 30 и
    // давала 47 кадров/с при потолке машины 60. Инерция 0.05 и порог 26
    // держат шаг 16 на 60-герцовой машине и всё так же роняют его до 66
    // на программном растеризаторе рельса (там дельта 60–100 мс).
    let shag = 16
    let srednyaya = 16
    let prev = 0
    // Время волны копится только по НАРИСОВАННЫМ кадрам: пока первый экран
    // вне окна, фаза стоит, и возвращение идёт без скачка.
    let tvolny = 0
    let poslednij = 0
    let pervyj = true
    const schet = () => { (window as unknown as { __kadry?: number }).__kadry = ((window as unknown as { __kadry?: number }).__kadry || 0) + 1 }

    const kadr = () => {
      if (ostanovlen) return
      ramka = requestAnimationFrame(kadr)
      const now = performance.now()
      const delta = prev ? now - prev : 16
      prev = now
      // Пауза одна и та же, что названа в контракте: первый экран ЦЕЛИКОМ
      // вне окна. Прокрутка волну больше не глушит.
      if (!vidno) { poslednij = now; return }
      // Допуск 5 мс. Без него дрожание rAF (16.7 ± 1 мс) роняло каждый
      // четвёртый кадр мимо порога 16 и давало 46 кадров/с при потолке 60
      // (замер proba/fps.mjs).
      if (!pervyj && now - poslednij < shag - 5) return
      srednyaya = srednyaya * 0.95 + Math.min(delta, 120) * 0.05
      shag = srednyaya > 45 ? 66 : srednyaya > 26 ? 33 : 16
      const proshlo = poslednij ? Math.min(now - poslednij, 100) : 0
      poslednij = now
      tvolny += proshlo / 1000
      if (razmer()) perepech()
      const amp = Math.min(1, (tvolny * 1000) / RAZGON_MS)
      gl!.uniform1f(uT, tvolny)
      gl!.uniform1f(uAmp, amp)
      gl!.drawArrays(gl!.TRIANGLES, 0, 3)
      // Подписи — тем же временем и той же амплитудой, в том же такте.
      if (podpisi.length) vezti(podpisi, cel.width / DPR(), cel.height / DPR(), tvolny, amp)
      schet()
      if (pervyj) { pervyj = false; gotova() }
    }
    kadr()
  }

  const zhdem = window.setTimeout(pusk, PAUZA_MS)

  // Первый экран ушёл из окна ЦЕЛИКОМ — волна встаёт (порог 0, rootMargin 0:
  // пока в окне хоть полоса первого экрана, волна идёт). Канва при этом
  // снимается с показа: даже нерисуемая канва 1440×900 стоит компоновщику
  // каждый кадр (замер раунда 3: показ канвы роняет страницу с 60 до 22).
  const nabl = new IntersectionObserver(([z]) => {
    vidno = z.isIntersecting
    cel.style.visibility = vidno ? '' : 'hidden'
  }, { threshold: 0, rootMargin: '0px' })
  const s1 = document.getElementById('s1')
  if (s1) nabl.observe(s1)

  return {
    stop: () => { ostanovlen = true; clearTimeout(zhdem); cancelAnimationFrame(ramka); nabl.disconnect() },
  }
}
