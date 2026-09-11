import { defineConfig, type Plugin } from 'vite'
import { BAZA } from './baza.mjs'

// Базовый путь — из BASE (умолчание «/»), одно место на сборку: site/baza.mjs.
//
// В корневом режиме vite'у отдаётся './', а не '/': раздача приёмки монтирует
// dist как корень, и относительная запись ассетов — то, что собиралось до
// введения BASE. Так `npm run build` без переменной даёт прежние байты.
// Под префиксом отдаётся сам префикс: vite перепишет ассеты и фавиконы
// в '/td-atlas/…'.
const base = BAZA === '/' ? './' : BAZA

// Ссылки, которые vite ассетами не считает и потому не трогает:
// href="/docs/" в шапке и в секции 5. Префиксуются здесь, после того как
// vite уже переписал настоящие ассеты, — иначе префикс встал бы дважды.
// При BASE=/ преобразование холостое: все адреса уже начинаются с '/'.
function prefiksVnutrennihAdresov(): Plugin {
  return {
    name: 'td-atlas-baza',
    transformIndexHtml: {
      order: 'post',
      handler(html: string) {
        return html.replace(/\b(href|src)="\/(?!\/)([^"]*)"/g, (vse, atr, hvost) => {
          const put = '/' + hvost
          return put.startsWith(BAZA) ? vse : `${atr}="${BAZA}${hvost}"`
        })
      },
    },
  }
}

export default defineConfig({
  base,
  plugins: [prefiksVnutrennihAdresov()],
  build: { outDir: 'dist', emptyOutDir: true, assetsInlineLimit: 0 },
})
