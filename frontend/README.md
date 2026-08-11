# ClaimIQ Frontend

Modern React dashboard for ClaimIQ — the pre-submission claim audit tool for Indian hospitals.

## Tech Stack

- **Next.js 14** (App Router)
- **TypeScript**
- **Tailwind CSS** (dark theme)
- **TanStack Query** (React Query) for data fetching
- **Recharts** for dashboard visualizations
- **Lucide React** for icons
- **react-dropzone** for file uploads

## Setup

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:3000` and proxies API calls to the ClaimIQ backend on `http://localhost:8000`.

## Running with the backend

1. Start the backend (from the project root):
   ```bash
   .\start.ps1
   ```
   Or manually:
   ```bash
   python -m uvicorn claimiq.api:app --port 8000
   ```

2. In a separate terminal, start the frontend:
   ```bash
   cd frontend
   npm run dev
   ```

3. Open `http://localhost:3000`

## CORS Configuration

The backend needs to allow requests from the frontend origin. Add this to your `.env`:

```
CLAIMIQ_CORS_ORIGINS=http://localhost:3000
```

## Pages

| Route | Description |
|-------|-------------|
| `/` | Upload bills and run audit — see what the insurer will deduct |
| `/claims` | View all previously audited claims |
| `/claims/[id]` | Detail view of a specific claim |
| `/dashboard` | Portfolio leakage analytics with charts |
| `/rules` | Browse the full rule catalog |

## Design

- Dark dashboard theme matching the existing ClaimIQ brand
- Color tokens: `accent` (orange), `hospital` (red), `patient` (blue), `settled` (green)
- Sidebar navigation with insurer profile selector
- Responsive layout with mobile hamburger menu
