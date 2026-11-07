# Brand Asset Usage

## The asset
The authentic Mintree wordmark is `frontend/public/brand-logo.svg` (viewBox 172 × 32, one colour, `#01877A`, transparent background, 7 vector paths). A byte-identical copy lives at `backend/app/assets/brand-logo.svg` for printable documents (the backend image does not contain the frontend folder). **No logo was redrawn, traced or approximated.** If the brand team supplies updated artwork, replace both files; nothing else changes.

## Variants and where each is used
| Variant | Where | How |
|---|---|---|
| Full wordmark, brand teal | Sidebar header (expanded), sign-in page, change-password page | `components/Logo.tsx` (`height` 20–26 px, proportions locked to the viewBox) |
| "M" mark, brand teal | Collapsed sidebar rail | `Logo mark` (crops the same paths to the leading M: viewBox 54 × 32) |
| "M" mark on white rounded square | Browser tab icon | `frontend/app/icon.svg` (the stock Next.js `favicon.ico` was removed) |
| Full wordmark, brand teal, 9 mm high, once per document | Tax invoice, certificate of analysis, purchase order, quotation, delivery challan | `backend/app/core/brand.py` (`header_flowable`): reads the SVG paths and draws them as vectors in the PDF (no raster, no scaling blur) |

Rules followed: no stretching (width derives from height), clear space of at least the mark height around the logo in the page header, transparent background, **one logo per document header** (never per table row or card), no logo on the small barcode labels (it would reduce scan-ability and legibility).

## Not done / not applicable
* Exports (CSV) carry no logo by nature.
* There is no splash/loading screen in the application (the loading state is a text placeholder); none was invented.
* A monochrome (all-black) variant for single-colour printing has not been supplied; `Logo` accepts a `color` prop (the web uses `currentColor`), the PDF header always uses the brand teal. Supply an approved mono artwork if black-and-white print runs are needed.
* Workspace/profile identity areas show the user's initials, not the logo (the logo identifies the product, not the person).
