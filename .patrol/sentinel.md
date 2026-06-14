# Sentinel — Security Patrol Log

## 2026-06-12
- Added security headers middleware (CSP, XFO, XCTO, Referrer-Policy) to FastAPI app — opened PR #13

## 2026-06-14
- Fixed unchecked file upload size in `/recipes/import` — `file.size` being `None` bypassed 5MB limit, enabling memory-exhaustion DoS — opened PR #TBD
