import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

export default defineConfig(({ mode }) => {
  const isLib = mode === 'lib'

  return {
    plugins: [react()],
    resolve: {
      alias: {
        'react-is': path.resolve(__dirname, 'src/shims/react-is.ts'),
      },
    },
    build: isLib
      ? {
        lib: {
          entry: path.resolve(__dirname, 'src/main.tsx'),
          name: 'Chatbot',
          fileName: () => `chatbot.js`,
          formats: ['umd']
        },
        rollupOptions: {
          external: [],
          output: {
            inlineDynamicImports: true
          }
        },
        assetsInlineLimit: Number.MAX_SAFE_INTEGER,  // ✅ Inline all assets as base64
        cssCodeSplit: false,   // ✅ merge all CSS into JS
        minify: true
      }
      : {},
  }
})
