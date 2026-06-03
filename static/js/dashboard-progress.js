(function () {
  const progress = () => document.getElementById('vc-page-progress');
  let completeTimer = null;

  function start() {
    const el = progress();
    if (!el) return;
    window.clearTimeout(completeTimer);
    el.classList.remove('is-complete');
    el.classList.add('is-active');
  }

  function done() {
    const el = progress();
    if (!el) return;
    el.classList.add('is-complete');
    completeTimer = window.setTimeout(() => {
      el.classList.remove('is-active', 'is-complete');
    }, 220);
  }

  function isPlainLeftClick(event) {
    return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  }

  function isDashboardPath(pathname) {
    return pathname === '/dashboard' || pathname.startsWith('/dashboard/');
  }

  function getLinkUrl(link) {
    const href = link && link.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) return null;
    return new URL(href, window.location.href);
  }

  function shouldDashboardBoostLink(link) {
    if (!link) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download')) return false;
    if (link.getAttribute('hx-boost') === 'false') return false;
    const url = getLinkUrl(link);
    if (!url) return false;
    if (url.origin !== window.location.origin) return false;
    if (url.pathname === window.location.pathname && url.hash) return false;
    if (url.searchParams.has('download')) return false;
    return isDashboardPath(url.pathname);
  }

  function shouldTrackLink(link, event) {
    if (!link || !isPlainLeftClick(event)) return false;
    return shouldDashboardBoostLink(link);
  }

  function markUnsafeBoostTargets(root) {
    const scope = root || document;
    scope.querySelectorAll('form:not([data-dashboard-boost])').forEach((form) => {
      form.setAttribute('hx-boost', 'false');
    });
    scope.querySelectorAll('a[href]').forEach((link) => {
      if (!shouldDashboardBoostLink(link)) {
        link.setAttribute('hx-boost', 'false');
      }
    });
  }

  document.addEventListener('click', function (event) {
    const link = event.target.closest('a[href]');
    if (!link || shouldDashboardBoostLink(link)) return;
    link.setAttribute('hx-boost', 'false');
    event.stopPropagation();
    if (!isPlainLeftClick(event)) return;
    if (link.target && link.target !== '_self') return;
    if (link.hasAttribute('download')) return;
    const url = getLinkUrl(link);
    if (!url || url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.hash) return;
    event.preventDefault();
    window.location.assign(url.href);
  }, true);

  document.addEventListener('click', function (event) {
    const link = event.target.closest('a[href]');
    if (shouldTrackLink(link, event)) start();
  });

  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!form || form.getAttribute('hx-boost') === 'false') return;
    const method = (form.method || 'get').toLowerCase();
    if (method === 'get' || form.dataset.progress === 'true') start();
  });

  document.addEventListener('htmx:beforeRequest', function (event) {
    const elt = event.detail && event.detail.elt;
    if (!elt) return;
    if (elt.closest && elt.closest('[data-no-progress]')) return;
    if (elt.matches && elt.matches('form[data-progress="true"]')) {
      start();
      return;
    }
    if (elt.matches && elt.matches('a[href]')) {
      if (shouldDashboardBoostLink(elt)) start();
    }
  });

  document.addEventListener('htmx:afterSwap', done);
  document.addEventListener('htmx:afterSwap', function (event) {
    markUnsafeBoostTargets(event.target);
  });
  document.addEventListener('htmx:responseError', done);
  document.addEventListener('htmx:sendError', done);
  document.addEventListener('DOMContentLoaded', function () {
    markUnsafeBoostTargets(document);
  });
  window.addEventListener('pageshow', done);
})();
