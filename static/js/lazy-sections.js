(function () {
  const queue = [];
  let active = 0;
  const maxConcurrent = 2;

  function schedule(section) {
    if (!section || section.dataset.lazyState) return;
    section.dataset.lazyState = 'queued';
    queue.push(section);
    pump();
  }

  function pump() {
    while (active < maxConcurrent && queue.length) {
      const section = queue.shift();
      load(section);
    }
  }

  function renderError(section) {
    section.innerHTML = [
      '<div class="rounded-2xl border border-red-100 bg-red-50 p-5 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200">',
      '<p class="font-bold">This section could not load.</p>',
      '<button type="button" class="mt-3 rounded-full bg-white px-3 py-1.5 text-xs font-bold text-red-700 shadow-sm dark:bg-vc-surface-raised dark:text-red-200" data-lazy-retry>Retry</button>',
      '</div>',
    ].join('');
    const retry = section.querySelector('[data-lazy-retry]');
    if (retry) {
      retry.addEventListener('click', function () {
        delete section.dataset.lazyState;
        section.innerHTML = section.dataset.lazySkeleton || '';
        schedule(section);
      }, { once: true });
    }
  }

  function load(section) {
    active += 1;
    section.dataset.lazyState = 'loading';
    if (!section.dataset.lazySkeleton) {
      section.dataset.lazySkeleton = section.innerHTML;
    }
    fetch(section.dataset.lazyUrl, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin',
    })
      .then((response) => {
        if (response.redirected) {
          const finalUrl = new URL(response.url, window.location.href);
          if (finalUrl.pathname.includes('/accounts/login')) {
            window.location.href = response.url;
            throw new Error('Authentication required');
          }
        }
        if (!response.ok) throw new Error('Section request failed');
        return response.text();
      })
      .then((html) => {
        section.dataset.lazyState = 'loaded';
        section.innerHTML = html;
        if (window.htmx) window.htmx.process(section);
        document.dispatchEvent(new CustomEvent('vc:lazy-section-loaded', { detail: { section } }));
      })
      .catch(() => {
        section.dataset.lazyState = 'error';
        renderError(section);
      })
      .finally(() => {
        active -= 1;
        pump();
      });
  }

  function init() {
    const sections = Array.from(document.querySelectorAll('[data-lazy-url]'));
    if (!sections.length) return;
    const observer = 'IntersectionObserver' in window
      ? new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              observer.unobserve(entry.target);
              schedule(entry.target);
            }
          });
        }, { rootMargin: '240px 0px' })
      : null;
    sections.forEach((section) => {
      if (observer) observer.observe(section);
      else schedule(section);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => requestAnimationFrame(() => requestAnimationFrame(init)));
  } else {
    requestAnimationFrame(() => requestAnimationFrame(init));
  }
  document.addEventListener('htmx:afterSwap', init);
})();
