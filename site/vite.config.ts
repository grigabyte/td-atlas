import { defineConfig } from 'vite'

// Раздача приёмки монтирует dist как корень, поэтому пути относительные.
export default defineConfig({
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, assetsInlineLimit: 0 },
})
