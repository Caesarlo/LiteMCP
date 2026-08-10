# LiteMCP Frontend

English | [简体中文](README.zh-CN.md)

Management console frontend for [LiteMCP](../README.md) — React 19, TypeScript, Vite, HeroUI, and Tailwind CSS. See the root [README](../README.md) and [frontend architecture](../docs/architecture/06-frontend.md) for the product-level picture; this document only covers working in `frontend/`.

> [!IMPORTANT]
> This is currently still the original HeroUI/Vite starter scaffold, not a LiteMCP product UI. It is not connected to any service-management API. It only proves the frontend toolchain runs. See [`../feature_list.json`](../feature_list.json) and [`../progress.md`](../progress.md) for the authoritative, verified state.

## Requirements

- A current Node.js LTS release
- npm

## Quick start

```bash
cd frontend
npm install
npm run dev
```

The dev server runs at `http://localhost:5173` by default.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the Vite dev server |
| `npm run build` | Type-check (`tsc`) and build for production (`vite build`) |
| `npm run test` | Run the test suite (Vitest) |
| `npm run lint` | Lint and auto-fix (ESLint) |
| `npm run preview` | Preview a production build locally |

## Project layout

```
frontend/
├── src/
│   ├── components/   # shared UI components
│   ├── layouts/       # page layout shells
│   ├── pages/          # route-level views
│   ├── config/         # frontend runtime configuration
│   ├── styles/          # Tailwind/global styles
│   ├── types/            # shared TypeScript types
│   └── test/               # test setup and utilities
├── public/
├── index.html
└── vite.config.ts
```

## Testing

Tests run on [Vitest](https://vitest.dev/) with [@testing-library/react](https://testing-library.com/docs/react-testing-library/intro/) and [MSW](https://mswjs.io/) for API mocking:

```bash
npm run test          # watch mode
npm run test -- --run # single run (used by CI / make ci-fast)
```

## Contributing

Run `npm run lint` and `npm run build` before committing. This project follows the repo-wide TDD and feature-verification workflow described in [`../AGENTS.md`](../AGENTS.md) — read it before starting non-trivial frontend work.
