// Разметка и тема сайта документации. Копия fanout/раунд-5/веер/shablon.mjs
// (замок d1) с одной перестройкой: всё, что веер брал из своего kontent.mjs
// (разделы, страницы, плашка), приходит сюда параметром — иначе один модуль
// не смог бы печатать и фикстуру п. 2 (тексты веера, старый снимок), и живые
// страницы (свежий снимок, два языка). Тема, CSS_D1, шапка, подвал, код,
// таблицы, меню, оглавление — байт в байт из веера.
//
// Проверка перестройки: `node site/generator/sborka.mjs --fixtura <кат>` печатает
// четыре файла, побайтно равные fanout/раунд-5/веер/d1/*.html (сверка —
// site/generator/sborka.mjs, ключ --sverit-veer). Пока эта сверка зелёная, п. 2
// меряет мой шаблон, а не копию чужого HTML.
//
// Шапка и подвал взяты из принятого лендинга раунда 4 дословно, с одним
// отступлением веера: у .shapka вместо rgba(16,16,16,.6) + backdrop-filter
// стоит непрозрачный var(--obsidian) — слово владельца 08.09, check.sh
// обязан давать exit 0.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { vstroke, esc } from './md.mjs'

const TUT = dirname(fileURLToPath(import.meta.url))
// Знак лежит рядом с генератором: после переезда исходников в репозиторий
// (11.09) каталога `лого/` рабочей папки под рукой нет, и сборка обязана
// собираться из чекаута.
const ZNAK = readFileSync(join(TUT, 'znak.svg'), 'utf-8')
  .replace('<svg', '<svg class="znak" aria-hidden="true" focusable="false"')
  .trim()

// ── тема ──────────────────────────────────────────────────────────────────
// :root — из лендинга дословно. Радиусы во всём сайте только 2px и 3px
// (правило 6 check.sh: больше двух уникальных значений — красное).
export const TEMA = `
:root{
  --obsidian:#101010; --bone:#fffdf9; --ash:#403f3f; --graphite:#495764;
  --seryj:#9a9a9a; --akcent:#8fe0ff; --plita:#0d0d0d; --orn:#7f7f7f;
  --setka:#1b1b1b; --shapka-h:56px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
html{scroll-behavior:auto}
body{background:var(--obsidian); color:var(--bone); font-family:Onest,system-ui,sans-serif;
     -webkit-font-smoothing:antialiased; padding-top:var(--shapka-h)}
.mono{font-family:'Martian Mono',ui-monospace,monospace; font-weight:300}

/* ── шапка лендинга ─────────────────────────────────────────────────────── */
.shapka{position:fixed; top:0; left:0; right:0; z-index:9; height:var(--shapka-h); display:flex;
        align-items:center; justify-content:space-between; padding:0 40px;
        background:var(--obsidian); border-bottom:1px solid var(--setka)}
.brend{display:flex; align-items:center; gap:10px}
.znak{display:block; flex:none; width:24px; height:24px; overflow:hidden; color:var(--bone)}
.marka{font-size:17px; font-weight:500; letter-spacing:-.01em; color:var(--bone)}
.yakorya{display:flex; gap:34px; align-items:center}
.yakorya a,.yakorya span{font-family:'Martian Mono',monospace; font-weight:300; font-size:11px;
        letter-spacing:.1em; color:var(--seryj); text-decoration:none}
.yakorya .akt{color:var(--bone); border-bottom:1px solid var(--akcent); padding-bottom:2px}
.sprava{display:flex; align-items:center; gap:18px}
.perekl{display:flex; align-items:center; gap:7px; font-family:'Martian Mono',monospace;
        font-weight:300; font-size:11px; letter-spacing:.1em}
.perekl a{color:var(--seryj); text-decoration:none}
.perekl b{font-weight:300; color:var(--bone)}
.perekl i{font-style:normal; color:var(--orn)}
.knopka{display:inline-flex; align-items:center; border:1px solid var(--bone); border-radius:2px;
        padding:8px 16px; color:var(--bone); background:none; cursor:pointer; text-decoration:none;
        font-family:'Martian Mono',monospace; font-weight:300; font-size:11px; letter-spacing:.1em;
        text-transform:uppercase}
.knopka.akc{border-color:var(--akcent); color:var(--akcent)}

/* ── подвал лендинга ────────────────────────────────────────────────────── */
.podval{position:relative; display:flex; align-items:center; height:96px; padding:0 64px;
        border-top:1px solid var(--ash); font-size:13px; color:var(--seryj)}
.podval a{color:var(--bone); text-decoration:none; border-bottom:1px solid var(--ash)}
.podval i{font-style:normal; padding:0 8px; color:var(--orn)}

/* ── проза ──────────────────────────────────────────────────────────────── */
main{display:grid; align-items:start}
.proza{min-width:0}
.proza h1{margin:0 0 14px; font-weight:400; font-size:40px; line-height:1.1; letter-spacing:-.02em}
.proza h2{margin:52px 0 16px; font-weight:400; font-size:26px; line-height:1.2; letter-spacing:-.015em}
.proza h3{margin:34px 0 12px; font-weight:500; font-size:19px; line-height:1.3}
.proza h1,.proza h2,.proza h3{scroll-margin-top:80px; position:relative}
.proza p{margin:0 0 18px; font-size:17px; line-height:1.62; color:var(--bone)}
.proza li{font-size:17px; line-height:1.62; margin:0 0 10px}
.proza a{color:var(--akcent); text-decoration:none; border-bottom:1px solid var(--graphite)}
.proza strong{font-weight:500}
.proza em{font-style:italic; color:var(--bone)}
.proza code{font-family:'Martian Mono',ui-monospace,monospace; font-weight:400; font-size:13px;
        color:var(--bone); background:var(--plita); border:1px solid var(--setka); border-radius:2px;
        padding:1px 5px; overflow-wrap:anywhere}
.yak{position:absolute; left:-24px; top:0; color:var(--orn); text-decoration:none; border:0;
     font-family:'Martian Mono',monospace; font-weight:300; font-size:15px}
.plashka{margin:0 0 34px; font-size:16px; color:var(--seryj)}
.plashka i{font-style:normal; padding:0 7px; color:var(--orn)}
.plashka a{color:var(--akcent); text-decoration:none; border-bottom:1px solid var(--graphite)}

/* ── код: монохром, без библиотек подсветки ─────────────────────────────── */
.kod{margin:0 0 24px; border:1px solid var(--ash); border-radius:3px; background:var(--plita)}
.kod .bar{display:flex; align-items:center; justify-content:space-between; gap:16px;
     padding:8px 10px 8px 16px; border-bottom:1px solid var(--setka)}
.kod .yaz{font-family:'Martian Mono',monospace; font-weight:300; font-size:11px;
     letter-spacing:.12em; text-transform:uppercase; color:var(--orn)}
.kod .knopka{padding:6px 12px}
.kopir{min-width:126px; justify-content:center}
.kod pre{margin:0; padding:16px; overflow-x:auto}
.kod code{font-family:'Martian Mono',ui-monospace,monospace; font-weight:400; font-size:13px;
     line-height:1.75; color:var(--bone); background:none; border:0; padding:0; white-space:pre;
     overflow-wrap:normal}
.kod .km{font-style:normal; color:var(--seryj)}
.kod .prompt{font-style:normal; color:var(--orn)}
.kod .kmd{font-style:normal; color:var(--akcent)}

/* ── таблицы: волосяные линейки, без заливки строк ──────────────────────── */
.tabl{margin:0 0 28px; overflow-x:auto}
table{border-collapse:collapse; width:100%; font-size:17px; line-height:1.55}
th{text-align:left; font-weight:500; font-size:16px; color:var(--seryj); padding:0 18px 10px 0;
   border-bottom:1px solid var(--ash); white-space:nowrap}
td{padding:14px 18px 14px 0; border-bottom:1px solid var(--setka); vertical-align:top}
th:last-child,td:last-child{padding-right:0}
.instr td.imya{font-family:'Martian Mono',monospace; font-weight:400; font-size:13px;
   color:var(--bone); white-space:nowrap}
.instr td.argi{font-family:'Martian Mono',monospace; font-weight:300; font-size:13px;
   color:var(--seryj); overflow-wrap:anywhere}
.instr td.zachem{font-size:17px}
.instr col.c1{width:19%}
.instr col.c2{width:22%}
.instr .metka{display:none}

/* ── меню разделов и оглавление ─────────────────────────────────────────── */
.eyebrow{font-family:'Martian Mono',monospace; font-weight:300; font-size:11px; letter-spacing:.14em;
   text-transform:uppercase; color:var(--orn); margin:0 0 14px}
.menu ul,.oglavlenie ul{list-style:none; margin:0; padding:0}
.menu a{display:block; padding:7px 0; font-size:16px; line-height:1.35; color:var(--seryj);
   text-decoration:none}
.menu .akt{color:var(--bone); border-left:1px solid var(--akcent); padding-left:12px; margin-left:-13px}
.oglavlenie a{display:block; padding:6px 0; font-size:16px; line-height:1.35; color:var(--seryj);
   text-decoration:none}
.oglavlenie .u3 a{padding-left:16px; font-size:16px; color:var(--orn)}
.oglavlenie .akt a{color:var(--bone)}
.razdely-knopka,.menu .zakryt{display:none}

/* ── 390 ────────────────────────────────────────────────────────────────── */
@media (max-width:860px){
  .shapka{padding:0 22px}
  .znak{width:20px; height:20px}
  .marka{font-size:16px}
  .yakorya{display:none}
  .sprava{gap:12px}
  .knopka{padding:7px 13px; font-size:10px}
  .podval{height:auto; padding:26px 22px; flex-wrap:wrap}
  .proza h1{font-size:30px}
  .proza h2{font-size:23px; margin-top:40px}
  .proza p,.proza li{font-size:17px}
  .yak{display:none}
  main{padding-left:22px; padding-right:22px}
  .kod .knopka{padding:6px 10px}
  .kopir{min-width:112px}

  /* таблица инструментов на 390 перестраивается в список: строки страницы
     не рвутся, горизонтальной прокрутки страницы нет */
  .instr table,.instr tbody,.instr tr,.instr td{display:block; width:auto}
  .instr thead,.instr colgroup{display:none}
  .instr tr{padding:16px 0; border-bottom:1px solid var(--setka)}
  .instr td{border:0; padding:0 0 6px}
  .instr td:last-child{padding-bottom:0}
  .instr td.imya{font-size:13px}
  .instr .metka{display:block; font-family:'Martian Mono',monospace; font-weight:300; font-size:11px;
     letter-spacing:.12em; text-transform:uppercase; color:var(--orn); padding-bottom:4px}
  .obychnaya table{font-size:16px}
  .obychnaya td,.obychnaya th{padding-right:12px}
}
`

// ── раскладка d1 замка: меню слева 236, текст 70ch, оглавление справа 232 ──
// Байт в байт блок CSS_D1 из fanout/раунд-5/веер/sborka.mjs.
// main padding-top 46 px (1440) и 26 px (390) — числа замка; они краснят
// пункт 3 «шкала 4 px» и это ДОПУСК ЗАМКА (ПРИЁМКА.md, раунд 5): строителю
// их править запрещено.
export const CSS_D1 = `
main{grid-template-columns:236px minmax(0,1fr) 232px; gap:0 44px; padding:46px 48px 84px}
.proza p,.proza ul,.proza h1,.proza h2,.proza h3,.proza .kod,.proza .obychnaya,.plashka{max-width:70ch}
.menu,.oglavlenie{position:sticky; top:calc(var(--shapka-h) + 46px); min-width:0}
.menu{border-right:1px solid var(--setka); padding-right:20px; margin-right:-20px}
.polosa{display:none}
@media (max-width:860px){
  main{grid-template-columns:minmax(0,1fr); padding:26px 22px 56px}
  body{padding-top:calc(var(--shapka-h) + 45px)}
  .polosa{display:flex; align-items:center; position:fixed; top:var(--shapka-h); left:0; right:0;
     z-index:8; height:45px; padding:0 22px; background:var(--obsidian);
     border-bottom:1px solid var(--setka)}
  .menu{display:none; position:fixed; top:calc(var(--shapka-h) + 45px); left:0; right:0; z-index:8;
     background:var(--obsidian); border-right:0; border-bottom:1px solid var(--ash);
     padding:16px 22px 22px; margin:0; max-height:calc(100vh - 101px); overflow-y:auto}
  .menu:target{display:block}
  .menu .zakryt{display:inline-flex; margin-top:16px}
  .proza h1,.proza h2,.proza h3{scroll-margin-top:125px}
  .oglavlenie{display:none}
}`

// Добавка живого сайта (в фикстуре её нет: файлы фикстуры одноязычны и без
// JS — ловушка 2 подготовителя). Ни одного нового радиуса и ни одного
// третьего шрифтового семейства: check.sh считает и то, и другое.
export const CSS_ZHIVOJ = `
/* Меню из 21 пункта (10.09, новая структура репозитория) на 1440x900
   занимает 774 px и кончается на 876-й: в окно влезает, в окно ниже
   880 px — нет, и хвост списка у прилипшего меню становится недостижим
   (замер 10.09: 1280x800 — «Для контрибьюторов» и «Что изменилось» за
   нижним краем). Прокрутка внутри колонки. Правило живёт ЗДЕСЬ, а не
   в CSS_D1: в фикстуре пункта 2 добавки живого сайта нет, и кадры
   замка d1 остаются 0.0000 %. Там, где меню и так помещается,
   ни одного изменения на экране: max-height больше содержимого. */
@media (min-width:861px){
  .menu{max-height:calc(100vh - var(--shapka-h) - 70px); overflow-y:auto;
    scrollbar-width:thin}
}
.perekl a{cursor:pointer}
html[data-lang="en"] .kopir{min-width:96px}
html[data-lang="en"] .ru-tolko{display:none}
html[data-lang="ru"] .en-tolko{display:none}
.knopka,.menu a,.oglavlenie a,.proza a,.perekl a{transition:color 200ms ease, border-color 200ms ease}
.knopka:hover{border-color:var(--akcent); color:var(--akcent)}
.menu a:hover,.oglavlenie a:hover,.perekl a:hover{color:var(--bone)}
@media (prefers-reduced-motion:reduce){
  .knopka,.menu a,.oglavlenie a,.proza a,.perekl a{transition:none}
}
.ukazatel{margin:0 0 28px}
.ukazatel a{display:block; padding:16px 0; border-bottom:1px solid var(--setka);
  color:var(--bone); text-decoration:none; border-left:0}
.ukazatel b{font-weight:500; font-size:19px}
.ukazatel span{display:block; margin-top:4px; font-size:16px; color:var(--seryj);
  overflow-wrap:anywhere}
.ukazatel a:hover b{color:var(--akcent)}
.proza ol{margin:0 0 18px; padding-left:22px}
.proza ul{margin:0 0 18px; padding-left:22px}

/* ── правка 2 доводки 09.09: «#» подзаголовка отходит от линии меню ──────
   Обмер раскладки d1 на 1440: колонка меню кончается линией на x = 304
   (48 padding + 236 колонка + 20 padding − 20 отрицательный margin), проза
   начинается на x = 328. Замок ставил .yak на left:-24px, то есть ровно
   на 304 — знак упирался в линию. −17px кладёт знак между ними: до линии
   ~7 px, до текста ~8 px. Правило живёт только в CSS_ZHIVOJ, поэтому
   фикстура пункта 2 и сверка с веером остаются байт в байт. */
.yak{left:-17px}

/* ── правка 1: подсветка текущего раздела в оглавлении ───────────────────
   Класс .akt переставляет скрипт-подсветка; красится только цвет —
   ни одного смещения, ни одной петли (ворота 4-доки). */
.oglavlenie li{transition:none}
.oglavlenie a{transition:color 200ms ease}

/* ── правка 3: отклик «Скопировать» как у CTA лендинга ───────────────────
   Устройство дословно с лендинга (лендинг-en/site/src/stil.css, «правка 5»):
   подпись живёт в span внутри кнопки, новая подпись лежит абсолютно и
   не может раздвинуть кнопку, заливка уходит в акцент. Кривая — ease:
   переменной --krivaya у доков нет, третьего семейства и нового радиуса
   правило не заводит (check.sh считает и то, и другое). */
.kod .knopka{position:relative; overflow:hidden}
.kod .knopka .pdp{display:inline-flex; align-items:center; gap:7px; white-space:nowrap}
.kod .knopka .pdp-nov{position:absolute; left:0; right:0; justify-content:center;
  opacity:0; transform:translateY(8px)}
.kod .knopka .pdp-nov svg{width:11px; height:11px; flex:none}
.kod .knopka{transition:background-color 200ms ease, color 200ms ease, border-color 200ms ease}
.kod .knopka .pdp{transition:opacity 200ms ease, transform 200ms ease}
.kod .knopka.gotovo{background:var(--akcent); border-color:var(--akcent); color:var(--obsidian)}
.kod .knopka.gotovo .pdp-est{opacity:0; transform:translateY(8px)}
.kod .knopka.gotovo .pdp-nov{opacity:1; transform:none}
@media (prefers-reduced-motion:reduce){
  .kod .knopka,.kod .knopka .pdp,.oglavlenie a{transition:none}
}

/* ── правка 5: пояснение замка на русских страницах ──────────────────────
   Вторая строка (только «Инструменты») отбита от первой на 18 px — тот же
   зазор, что между абзацами прозы. Первая строка на 390 переносится
   в три строки, и меньший зазор читался как её продолжение. */
.plashka + .plashka{margin-top:-16px}

/* ═══ ДОВОДКА 2 (09.09) ══════════════════════════════════════════════════ */

/* Зачёркнутый текст. Правило пришло в волну ради решения 60 «Решений»,
   которых на сайте больше нет (правка 2), а в TEMA оно ломало сверку
   фикстуры с веером: у веера этой строки нет. Место правила — живой CSS,
   тогда фикстура снова байт в байт (--sverit-veer). */
.proza del{text-decoration:line-through; color:var(--seryj)}

/* ── правка 3: диаграмма набирается одним шрифтом ────────────────────────
   Стек объявлен переменной, а не именем: правило 7 check.sh считает первые
   токены всех font-family и на третьем имени краснеет, а var(…) из счёта
   выброшено самим скриптом. Пункт 8 приёмки первый токен computed-стиля
   всё равно увидит («ui-monospace») — это названо в отчёте и ждёт допуска.
   Кегль 13 px — пол пункта 8 для кода. Межстрочный 1.25: рамки диаграммы
   смыкаются по вертикали, при 1.75 между рядами оставались просветы. */
:root{--sistemnyj-mono:ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace}
.proza .shema{max-width:70ch; margin:0 0 24px; padding:16px; border:1px solid var(--ash);
  border-radius:3px; background:var(--plita); overflow-x:auto}
.shema code{display:block; font-family:var(--sistemnyj-mono); font-weight:400; font-size:13px;
  line-height:1.15; color:var(--bone); background:none; border:0; border-radius:0; padding:0;
  white-space:pre; overflow-wrap:normal}

/* ── правка 4: панель разделов вместо полосы «Разделы» ───────────────────
   Значок в шапке выдвигает панель слева, задник затемняет страницу,
   закрывают крестик, клик мимо, выбор раздела и Esc (решение владельца
   09.09). Полосы под шапкой больше нет — отступ тела возвращается к высоте
   одной шапки. Панель ездит по действию человека: пункт 4-доки меряет
   петли и привязку к прокрутке, их по-прежнему 0.
   Скрытая панель убрана не display:none, а visibility: приёмка считает
   меню закрытым по display/visibility/высоте, и полсекунды перехода она
   бы застала «открытым». Отступы кратны 4 px — пункт 3 меряет шкалу и
   у невидимого узла тоже. */
/* Обеим кнопкам шрифт назначен явно, стеком тела: <button> без своего
   шрифта берёт шрифт формы из стилей браузера (Arial), и пункт 8 печатает
   лишнее семейство. Замер 09.09, шаг-11-ru: «Arial + Martian Mono + Onest
   + ui-monospace». Ключевое слово inherit тут не годится по другой причине:
   правило 7 check.sh считает первые токены и слово inherit засчитало бы
   как ещё одно имя (замер шаг-12-ru: «семейств больше двух (4)»). */
.knopka-menu{display:none; align-items:center; justify-content:center; width:32px; height:32px;
  margin-left:-8px; padding:0; border:0; background:none; color:var(--bone); cursor:pointer;
  font-family:Onest,system-ui,sans-serif}
.knopka-menu svg{display:block; width:16px; height:16px}
.menu .zakryt-panel{display:none; position:absolute; top:12px; right:12px; width:32px; height:32px;
  align-items:center; justify-content:center; padding:0; border:0; background:none;
  color:var(--seryj); cursor:pointer; font-family:Onest,system-ui,sans-serif}
.menu .zakryt-panel svg{display:block; width:14px; height:14px}
.knopka-menu:hover,.zakryt-panel:hover{color:var(--akcent)}
.sr{position:absolute; width:1px; height:1px; margin:-1px; padding:0; overflow:hidden;
  clip-path:inset(50%); white-space:nowrap; border:0}
.zaslon{display:none; position:fixed; inset:0; z-index:8; background:rgba(16,16,16,.72)}
/* Задник ниже шапки (z-index 9), чтобы шапка со значком оставалась читаемой
   и значок закрывал панель вторым нажатием; панель (11) выше задника. */
@media (max-width:860px){
  body{padding-top:var(--shapka-h)}
  .polosa{display:none}
  .knopka-menu{display:inline-flex}
  .menu .zakryt-panel{display:inline-flex}
  .menu{display:block; visibility:hidden; position:fixed; top:var(--shapka-h); bottom:0;
    left:0; right:auto; width:320px; max-width:86vw; max-height:none; z-index:11;
    padding:20px 20px 24px; border-right:1px solid var(--ash); border-bottom:0;
    overflow-y:auto; transform:translateX(-100%);
    transition:transform 200ms ease, visibility 0s linear 200ms}
  .menu.otkryt{visibility:visible; transform:none;
    transition:transform 200ms ease, visibility 0s linear 0s}
  .zaslon.otkryt{display:block}
  .proza h1,.proza h2,.proza h3{scroll-margin-top:80px}
}
@media (prefers-reduced-motion:reduce){
  .menu{transition:none}
  .menu.otkryt{transition:none}
}

/* ── РАУНД 8, находка 2: строчный код на переносе — один чип, не два ────
   Строка plugin/skills/touchdesigner/SKILL.md в прозе /install/ на 390
   рвётся на три куска, и по умолчанию (box-decoration-break:slice) рамка
   с отбивкой рисуются только на краях первого и последнего: куски
   читались как отдельные чипы с оборванными рамками. clone замыкает
   рамку на каждом куске.
   Правило в CSS_ZHIVOJ, а не в CSS_D1, по замеру: в CSS_D1 оно красит
   пункт 2 — отбивка появляется у каждого куска, строк становится больше,
   и фикстура d1-komandy-full-1440 вырастает на 26 px (прогон
   fanout/раунд-8/J-proba-p2). Живые страницы правку получают, фикстура
   замка остаётся 0.0000 %. */
.proza code{-webkit-box-decoration-break:clone; box-decoration-break:clone}

/* ── РАУНД 8, находка 1: длинная строка кода на телефоне не обрывается ───
   На 390 внутренняя ширина блока кода 344 px, а строка установки доходит
   до 796 px (замер fanout/раунд-8/kadry/probe.mjs): хвост уезжал за правый
   край, полосы прокрутки под накладными полосами macOS и iOS не видно, и
   блок читался как законченный (находка слепой приёмки раунда 7).
   На узком экране строка переносится; кнопка «Скопировать» по-прежнему
   отдаёт текст блока дословно — перенос только зрительный.
   Цена названа: столбцы выравненных комментариев на переносе разъезжаются.
   Правило живёт ЗДЕСЬ, а не в CSS_D1: в фикстуре пункта 2 добавок живого
   сайта нет, и кадры замка d1 остаются 0.0000 %. Живая страница на 390
   с кадром d1-ustanovka-390.png в зоне кода теперь не совпадает — это
   правка замка, разрешённая владельцем 12.09.
   Диаграммы (.shema) не трогаются: перенос ломает псевдографику. */
@media (max-width:860px){
  .kod code{white-space:pre-wrap; overflow-wrap:anywhere}
}
`

export const POLOSA_D1 = maketRazdely =>
  `<div class="polosa"><a class="knopka" href="#razdely">${maketRazdely}</a></div>`

// ── шапка ──────────────────────────────────────────────────────────────────
// ctx: { yakorya, perekl, docsImya, knopkaImya, knopkaHref }
// knopkaMenu — значок «разделы» слева от знака. Правка 4 доводки 2 (09.09):
// панель разделов выдвигается им, а полосы «Разделы» под шапкой больше нет.
// Ключ необязательный: фикстура пункта 2 его не передаёт и печатает шапку
// замка байт в байт (--sverit-veer).
export function shapka(ctx) {
  return `<header class="shapka">
  <span class="brend">
    ${ctx.knopkaMenu ? ctx.knopkaMenu + '\n    ' : ''}${ZNAK}
    <span class="marka">td-atlas</span>
  </span>
  <nav class="yakorya">${ctx.yakorya.map(y => `<a href="${y.href}"${y.dt ? ` data-t="${y.dt}"` : ''}>${y.imya}</a>`).join('')}<span class="akt" aria-current="page"${ctx.docsDt ? ` data-t="${ctx.docsDt}"` : ''}>${ctx.docsImya}</span></nav>
  <span class="sprava">
    <span class="perekl">${ctx.perekl}</span>
    <a class="knopka akc" href="${ctx.knopkaHref}"${ctx.knopkaDt ? ` data-t="${ctx.knopkaDt}"` : ''}>${ctx.knopkaImya}</a>
  </span>
</header>`
}

export const podval = `<footer class="podval">
  <span>© 2026 td-atlas</span><i>·</i><a href="https://github.com/grigabyte/td-atlas">GitHub</a>
</footer>`

// ── меню разделов ─────────────────────────────────────────────────────────
// punkty: [{ imya, href, akt, dt }]
// knopkaZakryt — крестик панели (правка 4 доводки 2, 09.09). Передан —
// встаёт первым узлом панели ВМЕСТО ссылки «Закрыть» внизу списка; не
// передан (фикстура) — печатается разметка замка байт в байт.
export function menu(punkty, { eyebrow = 'разделы', zakryt = 'Закрыть', eyebrowDt = null, zakrytDt = null, aria = 'разделы документации', knopkaZakryt = null } = {}) {
  const li = punkty.map(r =>
    `<li><a href="${r.href}"${r.dt ? ` data-t="${r.dt}"` : ''}${r.akt ? ' class="akt" aria-current="page"' : ''}>${r.imya}</a></li>`
  ).join('\n      ')
  return `<nav class="menu" id="razdely" aria-label="${aria}">
    ${knopkaZakryt ? knopkaZakryt + '\n    ' : ''}<p class="eyebrow"${eyebrowDt ? ` data-t="${eyebrowDt}"` : ''}>${eyebrow}</p>
    <ul>
      ${li}
    </ul>
    ${knopkaZakryt ? '' : `<a class="zakryt knopka" href="#verh"${zakrytDt ? ` data-t="${zakrytDt}"` : ''}>${zakryt}</a>\n  `}</nav>`
}

// ── оглавление страницы ───────────────────────────────────────────────────
// eyebrowDt / ariaDt — ключи хрома живого сайта (правка 1 доводки 09.09:
// «на странице» обязано переводиться вместе с остальным хромом). У фикстуры
// их нет и быть не должно: без них строка печатается байт в байт как у веера
// (--sverit-veer), а замок п. 2 меряется тем же шаблоном.
export function oglavlenie(bloki, { eyebrow = 'на странице', aria = 'на этой странице', eyebrowDt = null, ariaDt = null } = {}) {
  const punkty = bloki.filter(b => b.t === 'h2' || b.t === 'h3')
  if (!punkty.length) return ''
  const li = punkty.map((b, i) =>
    `<li class="u${b.t.slice(1)}${i === 0 ? ' akt' : ''}"><a href="#${b.id}"${i === 0 ? ' aria-current="true"' : ''}>${esc(b.tekst.replace(/`/g, ''))}</a></li>`
  ).join('\n      ')
  return `<aside class="oglavlenie" aria-label="${aria}"${ariaDt ? ` data-t="${ariaDt}" data-t-atr="aria-label"` : ''}>
    <p class="eyebrow"${eyebrowDt ? ` data-t="${eyebrowDt}"` : ''}>${eyebrow}</p>
    <ul>
      ${li}
    </ul>
  </aside>`
}

// ── код-блок: монохром своими руками, две краски ──────────────────────────
// Комментарий (` #…` вне кавычек) — серый; строка приглашения `$ ` — знак
// серый, команда акцентом. Больше ничего не красится, библиотек нет.
// Диаграмма — не код: правка 3 доводки 2 (09.09), СУЖЕНА доводкой 3 (09.09).
// Схемой считается блок, В КОТОРОМ ЕСТЬ СИМВОЛЫ ПСЕВДОГРАФИКИ, и только он
// (ПРИЁМКА.md, «Кнопка копирования у блоков-схем»). Прежнее правило «любой
// блок без указанного языка» отняло метку и кнопку у 20 блоков из 44, в том
// числе у настоящей команды `/plugin marketplace add grigabyte/td-atlas`.
// Блок без языка снова печатается кодом, метка языка у него прежняя — 'shell'
// (см. kodHTML). Печать схемы — shemaHTML, и только на живом сайте (ключ
// o.shemy). Фикстура пункта 2 ключа не получает и печатает всё тем же кодом,
// что веер: --sverit-veer остаётся байт в байт.
// U+2500–257F рамки, U+2580–259F плашки, U+25A0–25FF геометрия (► ▼ ● ■):
// ни один из этих блоков не входит в подмножество, которое грузит страница.
// Диапазон регулярного выражения — сплошной U+2500–25FF: замер 09.09 по
// снимку 0f6ea8e и переводу показал 0 блоков, которые попадали бы в него
// ТОЛЬКО плашками U+2580–259F, так что сплошной и две названные спекой
// полосы на этом корпусе — одно и то же множество.
export const ESHEMA = /[─-◿]/
export const eShema = b => ESHEMA.test(b.tekst)

// Псевдографика в Martian Mono не набирается: подмножество Google Fonts,
// которое грузит страница, кончается на U+2122 и не содержит U+2500–257F.
// Браузер подставляет системный моноширинный ДРУГОЙ ширины, и рамки
// диаграммы разъезжаются (жалоба владельца 09.09: «диаграммы сломаны»).
// Один шрифт на весь блок — рамки сходятся. Метки языка и кнопки
// копирования у диаграммы нет: подписывать «SHELL» дерево каталогов и
// предлагать его скопировать — обе вещи лишние (слово владельца).
// Тег не <pre>, а <figure>+<code>: это иллюстрация, а не команда. Следствие
// для мер: пункт 2а считает кнопки по числу <pre> и эти блоки не видит
// (иначе каждый из них шёл бы в «без кнопки»), пункт 7 не читает <code>,
// пункт 16 сравнивает по <pre> — блоки выпадают из сверки симметрично
// в обоих языках. Цена названа в отчёте.
export function shemaHTML(b) {
  return `<figure class="shema"><code>${esc(b.tekst)}</code></figure>`
}

export function kodHTML(b, podpis = 'Скопировать', o = {}) {
  if (o.shemy && eShema(b)) return shemaHTML(b)
  const stroki = b.tekst.split('\n').map(l => {
    const mp = l.match(/^(\s*)\$ (.*)$/)
    if (mp) {
      const [, pr, ost] = mp
      return `${pr}<i class="prompt">$</i> <i class="kmd">${esc(ost)}</i>`
    }
    const i = l.indexOf(' #')
    const kav = i > 0 && /['"]/.test(l.slice(0, i))
    if (i > 0 && !kav) return esc(l.slice(0, i)) + '<i class="km">' + esc(l.slice(i)) + '</i>'
    if (l.trimStart().startsWith('#')) return '<i class="km">' + esc(l) + '</i>'
    return esc(l)
  }).join('\n')
  return `<div class="kod">
  <div class="bar"><span class="yaz">${esc(b.yaz || 'shell')}</span>${podpis}</div>
  <pre><code>${stroki}</code></pre>
</div>`
}

export const KNOPKA_VEER = '<button class="knopka kopir" type="button">Скопировать</button>'
// Живая кнопка: подпись внутри кнопки (правило лендинга), data-t — чтобы
// подпись сменил тот же инлайновый скрипт языка, что и остальной хром.
export const KNOPKA_ZHIVAJA = '<button class="knopka kopir" type="button" data-t="kopir">Copy</button>'

// Подписи таблицы инструментов. RU — дословно из замка d1.
export const METKI_INSTR_RU = {
  metka: { imya: 'инструмент', argi: 'аргументы', zachem: 'для чего' },
  th: { imya: 'Инструмент', argi: 'Аргументы', zachem: 'Для чего' },
}
export const METKI_INSTR_EN = {
  metka: { imya: 'tool', argi: 'arguments', zachem: 'use it for' },
  th: { imya: 'Tool', argi: 'Arguments', zachem: 'Use it for' },
}

// ── блоки прозы ───────────────────────────────────────────────────────────
export function blokHTML(b, o = {}) {
  const knopka = o.knopka || KNOPKA_VEER
  switch (b.t) {
    case 'h1': return ''
    case 'h2':
    case 'h3':
    case 'h4': {
      const t = b.t
      return `<${t} id="${b.id}"><a class="yak" href="#${b.id}" aria-hidden="true">#</a>${vstroke(b.tekst)}</${t}>`
    }
    case 'p': return `<p>${vstroke(b.tekst)}</p>`
    // Перевод строки между <li> и между ячейками таблицы: в раскладке
    // он не рисуется (пробельный узел между блоками списка и между
    // </td><td> таблица выбрасывает), а в textContent он ЕСТЬ — и без
    // него соседние ячейки склеиваются в одно «число». На таблице
    // docs/decisions.md `| 7 | 2026-08-28 | …` пункт 7 читал «72026»
    // и не находил его в источнике (замер 09.09, шаг-01: 55 находок
    // на «Решениях» и 10 на «Формате», все до одной — стык ячеек).
    // Кадры от этого не меняются: сверка фикстуры с замком остаётся 0 %.
    case 'ul': return `<ul>${b.punkty.map(p => `<li>${vstroke(p)}</li>`).join('\n')}</ul>`
    case 'ol': return `<ol>${b.punkty.map(p => `<li>${vstroke(p)}</li>`).join('\n')}</ol>`
    case 'code': return kodHTML(b, knopka, { shemy: o.shemy })
    case 'table': {
      // Таблица систем в README идёт с пустой шапкой (`| | |`):
      // пустой ряд с линейкой не печатается.
      const pusto = b.shapka.every(c => !c)
      const th = b.shapka.map(c => `<th>${vstroke(c)}</th>`).join('\n')
      const tr = b.ryady.map(r => `<tr>${r.map(c => `<td>${vstroke(c)}</td>`).join('\n')}</tr>`).join('\n      ')
      return `<div class="tabl obychnaya"><table>
      ${pusto ? '' : `<thead><tr>${th}</tr></thead>`}
      <tbody>
      ${tr}
      </tbody></table></div>`
    }
    case 'instr': {
      // Умолчания — подписи замка d1 («ИНСТРУМЕНТ / АРГУМЕНТЫ / ДЛЯ ЧЕГО»);
      // живой сайт передаёт их же по-русски и английские на EN-теле.
      const m = o.metki || METKI_INSTR_RU
      // Перевод строки между меткой колонки и содержимым ячейки. На 1440
      // метка стоит display:none, но в textContent она ЕСТЬ и без
      // разделителя склеивается со следующим словом: «use it for2,060».
      // Пункт 16 читает «060» вместо «2,060», а «185» и «277» теряет
      // целиком (его regex не начинает число после буквы), и RU/EN
      // расходятся ТОЛЬКО потому, что русская метка кончается кириллицей,
      // а английская — латинской буквой. Замер 09.09, шаг-05-ru:
      // «/instrumenty/: числа вне кода RU 8, EN 6». Тот же род, что перевод
      // строки между </td> и <td> у волны.
      // Разделитель ставит ТОЛЬКО живой сайт (o.razdelMetki): у фикстуры
      // его нет, и она остаётся байт в байт равной вееру (--sverit-veer).
      const rz = o.razdelMetki || ''
      const tr = b.ryady.map(r => `<tr>
        <td class="imya"><span class="metka">${m.metka.imya}</span>${rz}<code>${esc(r.imya)}</code></td>
        <td class="argi"><span class="metka">${m.metka.argi}</span>${rz}${esc(r.argi)}</td>
        <td class="zachem"><span class="metka">${m.metka.zachem}</span>${rz}${vstroke(r.zachem)}</td>
      </tr>`).join('\n      ')
      return `<div class="tabl instr"><table>
      <colgroup><col class="c1"><col class="c2"><col class="c3"></colgroup>
      <thead><tr><th>${m.th.imya}</th><th>${m.th.argi}</th><th>${m.th.zachem}</th></tr></thead>
      <tbody>
      ${tr}
      </tbody></table></div>`
    }
    default: throw new Error('нет разметки для блока ' + b.t)
  }
}

// ── проза страницы ────────────────────────────────────────────────────────
// plashka — готовый HTML или ''. h1 — заголовок страницы.
export function prozaHTML({ h1, bloki, plashka = '', knopka, metki, razdelMetki, shemy = false }) {
  // Первый блок, повторяющий H1 страницы (в EN-ломтике это «## Install»),
  // не печатается: заголовок страницы уже стоит выше.
  const b = bloki.filter((x, i) => !(i === 0 && x.t === 'h2' && x.tekst === h1))
  const telo = b.map(x => blokHTML(x, { knopka, metki, razdelMetki, shemy })).join('\n')
  return `<article class="proza">
    <h1 id="verh">${esc(h1)}</h1>
    ${plashka}
    ${telo}
  </article>`
}

// ── страница ──────────────────────────────────────────────────────────────
// Один каркас на фикстуру и на живой сайт. Разница ровно в четырёх
// параметрах: комментарий, favicon, скрипт языка в <head> (golova) и
// вызов подстановки перед </body> (hvost). Пустые — фикстура; тогда
// строка получается байт в байт такой, какую печатает веер.
export function stranica({ kommentarij, yaz, titul, favicon, dopCSS, klassTela, telo, golova = '', hvost = '' }) {
  return `<!doctype html>
<html lang="${yaz}">
<!--
${kommentarij}
-->
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(titul)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500&family=Martian+Mono:wght@300;400&display=swap&subset=cyrillic,latin" rel="stylesheet">
<link rel="icon" href="${favicon}">
<style>${TEMA}${dopCSS}</style>${golova}
</head>
<body class="${klassTela}">
${telo}
${podval}${hvost}
</body>
</html>
`
}
