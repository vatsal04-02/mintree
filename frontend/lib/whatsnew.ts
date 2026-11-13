/** Release notes shown in the "What's new" panel. Static, typed and written by hand: add the newest entry at the top. */
export type Release = { date: string; title: string; items: string[] };

export const WHATS_NEW: Release[] = [
  { date: "2026-11-12", title: "Quick actions and faster navigation", items: [
    "The dock on the left holds your pinned actions; choose which ones with the pencil.",
    "Press Ctrl K for actions as well as pages, and ? for every keyboard shortcut.",
    "Star a page to keep it at the top of the sidebar; recent pages are listed under it.",
    "Approving, rejecting, moving a deal and marking an alert read can be undone for four seconds.",
  ] },
  { date: "2026-11-12", title: "Customers, Accounts and Growth", items: [
    "A customer directory with a card view, and an account page for each customer.",
    "Accounts shows cash flow and receivables ageing; open any invoice as a paper invoice.",
    "Management has a Growth section with 3, 6 and 12 month views and CSV export.",
  ] },
];
