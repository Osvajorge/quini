import html from 'eslint-plugin-html';
import globals from 'globals';

export default [
  {
    ignores: ['.venv/**', '.wrangler/**', 'node_modules/**', 'data/**', 'docs/data/**', 'backtest/reports/**'],
  },
  {
    files: ['docs/**/*.html'],
    plugins: { html },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: { ...globals.browser },
    },
    rules: {
      'no-unused-vars': 'error',
      curly: ['error', 'all'],
      eqeqeq: ['error', 'smart'],
      'no-eval': 'error',
      'no-implied-eval': 'error',
    },
  },
  {
    files: ['functions/**/*.js', 'docs/sw.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.worker, ...globals.serviceworker },
    },
    rules: {
      'no-unused-vars': 'error',
      curly: ['error', 'all'],
      eqeqeq: ['error', 'always'],
      'no-eval': 'error',
      'no-implied-eval': 'error',
    },
  },
];
