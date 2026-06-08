# Arova Performance Playbook

## Overview
This file captures the patterns Arova uses to make the app feel fast.

Some of these tactics improve **actual latency** by reducing server work, query volume, or template/render overhead. Others improve **perceived responsiveness** by giving users immediate visual feedback, rendering a fast shell first, and deferring heavy work until it is needed.

This is written as a **portable playbook**. Each pattern includes the Arova implementation, but the goal is to help you extract and reuse the idea in other projects.

Only strategies that are implemented in this repo today are included here.

## Performance Strategy Catalog

### Perceived speed / UI responsiveness

**Strategy:** Navigation progress bars for page transitions  
**What it solves:** Full-page navigations and form submits can feel dead even when the backend is fast.  
**Arova implementation:** Dashboard, ops, and admin shells start a top progress bar on internal link clicks and form submits, then clear it when the next page or admin partial is ready.  
**Where it appears:** `templates/dashboard/base.html`, `templates/ops/base.html`, `static/js/admin-progress.js`, `templates/admin/base.html`, `static/css/dashboard.css`  
**How to reuse it:** Add a lightweight shell-level progress bar that only triggers for same-origin navigations and form posts. Treat it as feedback, not as a real network meter.  
**Watch-outs:** Exclude hash links, downloads, blank targets, and modifier-clicks, or the bar will fire on actions that do not navigate.

**Strategy:** Skeleton placeholders for heavy panels and tables  
**What it solves:** Empty containers make slow sections feel broken. Skeletons make loading states feel intentional and stable.  
**Arova implementation:** Heavy dashboard panels, analytics sections, admin sections, and several data tables render skeleton rows/cards/charts before real content appears.  
**Where it appears:** `templates/dashboard/home.html`, `templates/ops/home_manager.html`, `templates/ops/analytics.html`, multiple dashboard list templates, `static/css/dashboard.css`, `static/css/admin_arova.css`  
**How to reuse it:** Design the placeholder first so layout, spacing, and height closely match the loaded content. This reduces layout shift and gives the page a “ready immediately” feel.  
**Watch-outs:** Do not skeletonize content that can render immediately; otherwise you just delay useful pixels.

**Strategy:** Fast shell first, heavy content later  
**What it solves:** Big dashboards often block on charts, activity feeds, and summary tables.  
**Arova implementation:** The main dashboard page renders the shell, banner, metrics, and layout immediately while heavy panels load via fragment endpoints.  
**Where it appears:** `dashboard/views.py` (`DashboardHomeView` and fragment views), `ops/views.py`, `templates/dashboard/home.html`, `templates/ops/home_manager.html`  
**How to reuse it:** Split the page into a fast shell and a few independently loadable sections. Keep the shell useful on its own.  
**Watch-outs:** Do not defer critical-above-the-fold content that users need immediately to orient themselves.

**Strategy:** Theme bootstrap before CSS loads  
**What it solves:** Theme flash causes a visible repaint and makes the UI feel unstable on load.  
**Arova implementation:** The saved theme is read from `localStorage` before the main dashboard stylesheet is applied, so dark mode is present on first paint.  
**Where it appears:** `templates/components/theme_bootstrap.html`, tested in `dashboard/tests.py`  
**How to reuse it:** Run a tiny inline bootstrap script in the `<head>` before your main CSS bundle. Only set the minimal attributes needed for first paint.  
**Watch-outs:** Keep the bootstrap tiny and defensive; if it throws, it should fail to a sane default.

**Strategy:** Persisted sidebar collapse state  
**What it solves:** Repeatedly re-expanding the same layout state makes the app feel clumsy and reset-heavy.  
**Arova implementation:** Sidebar collapse/expand state is stored in `localStorage` and restored on page load.  
**Where it appears:** `templates/dashboard/base.html`, `templates/ops/base.html`, `static/css/dashboard.css`  
**How to reuse it:** Persist stable shell preferences that users repeat often, especially navigation density and panel state.  
**Watch-outs:** Scope stored UI state carefully if multiple shells or roles share the same browser.

### Deferred loading / front-end scheduling

**Strategy:** Lazy section loading with fragment URLs  
**What it solves:** Large pages with multiple heavy blocks do too much work up front.  
**Arova implementation:** Any section with `data-lazy-url` is fetched independently after the shell renders.  
**Where it appears:** `static/js/lazy-sections.js`, dashboard and ops home templates, ops analytics template, admin dashboard partials  
**How to reuse it:** Move heavyweight panels into small server-rendered fragment endpoints and hydrate them into shell placeholders.  
**Watch-outs:** Fragment endpoints still need correct auth, scope checks, and cache keys; do not treat them as “just frontend.”

**Strategy:** `IntersectionObserver`-gated loading with root margin  
**What it solves:** Loading every deferred section immediately still creates a burst of network and render work.  
**Arova implementation:** Lazy sections and charts wait until they are near the viewport before loading. Root margins are used so work starts slightly before the section scrolls into view.  
**Where it appears:** `static/js/lazy-sections.js`, `static/js/home-charts.js`  
**How to reuse it:** Use viewport proximity, not just DOM presence, as the trigger for expensive work.  
**Watch-outs:** Tune root margins to preload just enough. Too small creates pop-in; too large defeats the optimization.

**Strategy:** Bounded concurrency for lazy requests  
**What it solves:** Lazy loading can still overload the browser or server if every section fires at once.  
**Arova implementation:** The shared lazy loader processes at most two section requests at a time.  
**Where it appears:** `static/js/lazy-sections.js`  
**How to reuse it:** Add a small queue with a concurrency cap when one screen can spawn many fragment requests.  
**Watch-outs:** Very low concurrency can make the page feel serialized; very high concurrency recreates the original load spike.

**Strategy:** Startup work staged with `requestAnimationFrame`  
**What it solves:** Initializing deferred behavior too early can compete with first paint.  
**Arova implementation:** Lazy-section initialization is staged through nested `requestAnimationFrame` calls so the browser gets a chance to paint first.  
**Where it appears:** `static/js/lazy-sections.js`  
**How to reuse it:** Schedule non-critical initialization just after initial paint, especially for observers and background loaders.  
**Watch-outs:** Do not use this for work that is required before interaction.

**Strategy:** On-demand chart booting with `requestIdleCallback` / observer fallback  
**What it solves:** Chart libraries are expensive to boot and should not block shell interactivity.  
**Arova implementation:** Home charts load when visible, and older/fallback paths use idle-time scheduling before initialization.  
**Where it appears:** `static/js/home-charts.js`  
**How to reuse it:** Defer chart library setup until the chart is near the viewport or the browser is idle.  
**Watch-outs:** Always keep a fallback for environments without `requestIdleCallback`.

**Strategy:** Retryable lazy section failures  
**What it solves:** A failed deferred fetch should not leave a dead blank panel.  
**Arova implementation:** Lazy sections render a small error state with a retry button that resets the section and re-observes it.  
**Where it appears:** `static/js/lazy-sections.js`  
**How to reuse it:** Give each deferred block its own failure/retry behavior so one bad panel does not poison the whole page.  
**Watch-outs:** Reset all relevant section flags before retrying, or the block may stay stuck in an initialized state.

### Caching

**Strategy:** Redis-backed application cache  
**What it solves:** Recomputing hot dashboard/admin aggregates on every request wastes time and database work.  
**Arova implementation:** Django uses `django-redis` as the default cache backend, with TTLs for dashboard, consumer, and admin workloads.  
**Where it appears:** `arova_core/settings.py`, `requirements.txt`  
**How to reuse it:** Pick a shared cache backend first, then centralize TTLs for each workload type.  
**Watch-outs:** Cache configuration is infrastructure, not just app code. Bad eviction or unavailable Redis will change behavior quickly.

**Strategy:** Computed dashboard summary cache with scoped keys  
**What it solves:** Dashboard home uses the same expensive aggregates across multiple fragments and shell elements.  
**Arova implementation:** A single summary object is computed once, keyed by tenant, role, selected branch, available branches, and billing version, then reused by the dashboard shell and fragment endpoints.  
**Where it appears:** `dashboard/home_services.py`, `dashboard/views.py`, `dashboard/tests.py`  
**How to reuse it:** Cache high-value aggregate view models rather than caching many small queries independently.  
**Watch-outs:** Scope keys must include all dimensions that materially change the result set, especially tenant and branch/user visibility.

**Strategy:** Template fragment caching for expensive sections  
**What it solves:** Even after data is computed, rendering charts and analytics sections repeatedly still costs CPU and template time.  
**Arova implementation:** Dashboard charts and ops analytics fragments are wrapped in Django template fragment caches keyed by scope and filter inputs.  
**Where it appears:** `templates/dashboard/fragments/_charts.html`, `templates/ops/fragments/_analytics_section.html`  
**How to reuse it:** Cache rendered fragments when the HTML itself is expensive and the input shape is easy to key.  
**Watch-outs:** Fragment caches must vary on all filters that change visible output, not just the URL path.

**Strategy:** Cached Django template loader outside local dev  
**What it solves:** Re-reading and re-parsing templates on every request adds avoidable overhead in non-dev environments.  
**Arova implementation:** Production-like environments switch to `django.template.loaders.cached.Loader`; local dev keeps normal loaders for ergonomics.  
**Where it appears:** `arova_core/settings.py`, validated in `dashboard/tests.py`  
**How to reuse it:** Use cached template loaders in environments where template hot-reload is not needed.  
**Watch-outs:** Do not enable cached loaders in local development unless your team is comfortable restarting for template changes.

**Strategy:** Admin sidebar navigation cache keyed by schema and permission shape  
**What it solves:** Building dynamic admin navigation repeatedly is wasteful, especially in multi-tenant and role-sensitive setups.  
**Arova implementation:** Admin sidebar output is cached by admin site, schema, and a permission signature derived from visible modules. Active-state flags are neutralized before caching.  
**Where it appears:** `arova_core/admin_utils.py`, `dashboard/tests.py`  
**How to reuse it:** Cache structural navigation separately from per-request active-state logic.  
**Watch-outs:** Never cache user-specific nav trees without including permission shape or equivalent access scope in the key.

**Strategy:** Admin dashboard aggregate caches  
**What it solves:** Cross-tenant admin reporting is expensive and should not recompute on every load.  
**Arova implementation:** Platform KPI cards, trends, merchant breakdowns, activity, and merchant lists are cached behind small helper wrappers with shared TTL handling.  
**Where it appears:** `arova_core/admin_dashboard_service.py`  
**How to reuse it:** Cache expensive cross-tenant or cross-domain aggregations near the service layer, not only at the template layer.  
**Watch-outs:** Global/admin caches need careful invalidation windows because they can hide fast-moving operational changes.

### Data/query optimization

**Strategy:** `select_related` / `prefetch_related` on hot paths  
**What it solves:** N+1 query patterns make dashboards and lists feel unpredictable and slow under load.  
**Arova implementation:** Hot dashboard, admin, and analytics queries aggressively shape related-object loading ahead of time.  
**Where it appears:** `dashboard/home_services.py`, `dashboard/views.py`, `ops/analytics_services.py`, `arova_core/admin_utils.py`, `arova_core/admin_dashboard_service.py`  
**How to reuse it:** Identify the exact related objects used in templates/serializers and fetch them in one shaped query.  
**Watch-outs:** Over-prefetching can be as harmful as under-fetching. Shape queries to the concrete UI, not the whole model graph.

**Strategy:** Centralized branch/customer scope helpers  
**What it solves:** Multi-tenant, multi-branch apps often overfetch and filter too late.  
**Arova implementation:** Shared helpers apply branch and customer scope early, and those scoped querysets are reused for dashboard summaries, fragments, and search results.  
**Where it appears:** `dashboard/home_services.py`, `dashboard/views.py`, `ops/views.py`  
**How to reuse it:** Put scope logic in reusable query helpers so performance and access control move together.  
**Watch-outs:** Scope helpers should return the narrowest correct queryset; late filtering wastes both time and memory.

**Strategy:** Small, intentional result caps on live panels and search  
**What it solves:** “Helpful” endpoints often become slow because they return too much data.  
**Arova implementation:** Recent activity, recent transactions, top customers, leaderboard rows, and global search all limit results to small lists.  
**Where it appears:** `dashboard/home_services.py`, `dashboard/views.py`, global search views in `dashboard/views.py`  
**How to reuse it:** Cap interactive surfaces aggressively; users perceive a fast top 5 more positively than a slow top 50.  
**Watch-outs:** Pair small caps with clear “view more” routes when deeper exploration is needed.

### Interaction shaping

**Strategy:** Debounced global search and customer lookup  
**What it solves:** Firing requests on every keystroke creates noisy, wasteful interaction patterns.  
**Arova implementation:** Global search, transaction customer lookup, and redemption search all wait briefly after typing before sending requests.  
**Where it appears:** `templates/dashboard/base.html`, `templates/ops/base.html`, `templates/dashboard/transactions/list.html`, `templates/ops/transactions/staff_list.html`, `templates/staff/transactions_list.html`, `templates/staff/redemptions_list.html`  
**How to reuse it:** Debounce network-backed text search and combine it with minimum query lengths and small response sizes.  
**Watch-outs:** Excessive debounce delays make the UI feel sleepy. Use short delays and clear empty/loading states.

**Strategy:** Progressive fragment loading instead of blocking a whole page  
**What it solves:** One slow section should not hold the rest of the page hostage.  
**Arova implementation:** Dashboard and analytics screens render shell HTML first and let slower sections arrive independently.  
**Where it appears:** `dashboard/views.py`, `ops/views.py`, `templates/dashboard/home.html`, `templates/ops/analytics.html`  
**How to reuse it:** Decompose pages into independently useful blocks so the first screen is interactive before every insight is loaded.  
**Watch-outs:** Progressive loading works best when the shell has real value; a shell full of placeholders is only marginally better than a spinner.

## Extraction Checklist
- Configure a real cache backend before adding cache-aware view logic.
- Separate page shells from heavy fragment endpoints.
- Design skeleton and placeholder states before deferring content.
- Add a shared lazy loader before creating many one-off panel loaders.
- Scope cache keys by tenant, role, branch, filters, and any other visibility boundary.
- Verify hot-path query shaping with `select_related` / `prefetch_related`.
- Cap interactive result sets such as search, “recent,” and leaderboard lists.
- Pair debounced search with minimum query lengths and clear loading/empty/error states.
- Keep navigation-state persistence small and local to the shell.
- Add tests around cache key separation and environment-specific template loading.

## What Not to Copy Blindly
- **Stale cache risks:** Short TTL caches still serve stale data. Pick freshness windows deliberately.
- **Scope leaks in cache keys:** Missing tenant/branch/role dimensions can leak data or show the wrong aggregates.
- **Lazy-loading too much above the fold:** If the first screen is mostly placeholders, the page still feels slow.
- **Overusing skeletons:** If real content could render immediately, skeletons become theater instead of value.
- **Perceived-speed tricks without backend work:** Progress bars and placeholders cannot rescue bad queries or oversized payloads.
- **Unbounded deferred fetches:** Lazy loading without concurrency limits can just move the load spike to after first paint.
- **Blindly copying admin/global caches:** Cross-tenant caches need stronger key discipline and clearer invalidation rules than single-tenant screens.

