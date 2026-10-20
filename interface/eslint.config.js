import eslint from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: ['**/dist/**', '**/node_modules/**', '**/.vite/**'],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // The terminal client exists to emit and measure ANSI sequences, so
    // matching control characters in a pattern is deliberate in this project.
    rules: {
      'no-control-regex': 'off',
    },
  },
  {
    files: ['**/*.ts', '**/*.tsx'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-empty-object-type': 'off',
    },
  },
  {
    // The terminal client and the web bridge both run on Node.
    files: ['packages/core/**/*.ts', 'packages/cli/**/*.{ts,tsx}', 'packages/web/vite.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
  {
    // The browser bundle.
    files: ['packages/web/src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    files: ['**/test/**/*.mjs', '**/*.test.mjs'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
);
