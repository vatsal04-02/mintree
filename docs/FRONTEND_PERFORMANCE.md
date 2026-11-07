# Frontend Performance

## Audit method
Static review of every data-fetching hook and shell component, plus the Playwright suites (they fail on any console error). No browser profiling traces were captured in this environment, so **no Lighthouse/Web-Vitals numbers are claimed**.

## Findings and what was done
| # | Finding | Action | Evidence |
|---|---|---|---|
| F1 | Overview and Management dashboards each fetched `management-summary` on every visit; navigating back and forth re-ran the heavy aggregate | `sharedGet` (`lib/api.ts`): in-flight de-duplication + 10 s freshness for dashboards, 30 s for the AI status flag; **never** used for stock availability, balances or anything acted on within seconds | `lib/summary.ts`, `components/AssistantPanel.tsx` |
| F2 | The server ran the dashboard queries for every viewer | 15 s server cache (single-flight) with `meta.generated_at/cached/max_age_s`; the Overview now states "computed N s ago (refreshed at most every 15 s)" so a cached figure is never presented as live | `analytics/router.py`, Overview header |
| F3 | Reference lists (items, units, warehouses, customers…) hit the database per request | versioned response cache on the server (invalidated by writes) | `core/rcache.py` |
| F4 | Assistant panel was mounted inside the header whose backdrop filter made `position: fixed` relative to the header (clipped to 56 px) | panel lifted to the shell root; memoised toggle | `AppShell.tsx` |
| F5 | Notification bell polls every 60 s | unchanged (a single cheap request per minute); documented |  |
| F6 | Large tables | server-side pagination is used by every list screen (`page`, `page_size`), so nothing renders thousands of rows; virtualisation was **not** added because no screen renders more than one page | `components/DataTable.tsx`, list endpoints |

## Rules that stay
* Numbers, money, dates and sizes are formatted only in `lib/format.ts` (ESLint-enforced), so every module reads the same.
* Stock allocation screens read fresh data; cached aggregates are labelled with their age.

## Not done
* Route-level code splitting beyond Next.js defaults, image optimisation (the app has no raster images), and optimistic updates: no measurement showed a need, and optimistic updates on stock/finance screens would risk misleading users.
* No automated bundle-size budget; a production build is part of every browser-test run and succeeds.
