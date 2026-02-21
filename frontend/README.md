# Frontend (React + Vite)

## Local dev

1. Install dependencies:

```bash
npm install
```

2. Start dev server:

```bash
npm run dev
```

## API base URL

Set `VITE_API_BASE_URL` to point at your backend.

1. Behind Caddy routing (`/api/*` -> backend): `VITE_API_BASE_URL=/api`
2. Direct backend access: `VITE_API_BASE_URL=http://localhost:8000`
