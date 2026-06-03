(function () {
  const chartJsUrl = 'https://cdn.jsdelivr.net/npm/chart.js';
  let chartJsPromise = null;

  function ensureChartJs() {
    if (window.Chart) return Promise.resolve();
    if (chartJsPromise) return chartJsPromise;
    chartJsPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = chartJsUrl;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Chart.js failed to load'));
      document.head.appendChild(script);
    });
    return chartJsPromise;
  }

  function bootWhenReady(canvasId, factory) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || canvas.dataset.chartReady === 'true' || canvas.dataset.chartReady === 'pending') return;
    const start = () => {
      if (canvas.dataset.chartReady === 'true' || canvas.dataset.chartReady === 'loading') return;
      canvas.dataset.chartReady = 'loading';
      const chartReady = ensureChartJs();
      const run = () => {
        chartReady
          .then(() => {
            if (!document.body.contains(canvas)) return;
            factory(canvas);
            canvas.dataset.chartReady = 'true';
          })
          .catch(() => {
            canvas.dataset.chartReady = 'error';
          });
      };
      if ('requestIdleCallback' in window) {
        window.requestIdleCallback(run, { timeout: 900 });
      } else {
        window.setTimeout(run, 0);
      }
    };
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            observer.disconnect();
            start();
          }
        });
      }, { rootMargin: '180px 0px' });
      canvas.dataset.chartReady = 'pending';
      observer.observe(canvas);
    } else {
      start();
    }
  }

  window.vcBootChart = bootWhenReady;

  function readJson(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent);
    } catch (error) {
      return fallback;
    }
  }

  function initHomeCharts() {
    const data = readJson('dashboardHomeChartData', null);
    if (!data) return;
    bootWhenReady('moneyFlowChart', function (moneyCtx) {
      const existing = Chart.getChart(moneyCtx);
      if (existing) {
        existing.destroy();
      }
      new Chart(moneyCtx, {
        type: 'bar',
        data: {
          labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
          datasets: [{
            label: 'Votes',
            data: data.monthly_votes || [],
            backgroundColor: '#191bdf',
            borderRadius: 6,
            barThickness: 8,
            maxBarThickness: 12,
          }, {
            label: 'Revenue (GHS)',
            data: data.monthly_revenue || [],
            backgroundColor: '#b8b9f5',
            borderRadius: 6,
            barThickness: 8,
            maxBarThickness: 12,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#09080d',
              padding: 12,
              titleFont: { family: "'DM Sans', sans-serif", size: 13, weight: '700' },
              bodyFont: { family: "'DM Sans', sans-serif", size: 12 },
              cornerRadius: 8,
              displayColors: false,
              callbacks: {
                label: function (context) {
                  let label = context.dataset.label || '';
                  if (label) label += ': ';
                  if (context.parsed.y !== null) {
                    label += context.datasetIndex === 1
                      ? 'GHS ' + context.parsed.y.toLocaleString()
                      : context.parsed.y.toLocaleString();
                  }
                  return label;
                }
              }
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: '#f0f2ff', drawBorder: false },
              ticks: { color: '#a9a9af', font: { family: "'DM Sans', sans-serif", size: 10, weight: '600' } },
              border: { display: false }
            },
            x: {
              grid: { display: false, drawBorder: false },
              ticks: { color: '#a9a9af', font: { family: "'DM Sans', sans-serif", size: 11, weight: '600' } },
              border: { display: false }
            }
          }
        }
      });
    });

    const nominees = data.top_nominees || [];
    if (!nominees.length) return;
    bootWhenReady('voteShareChart', function (voteCtx) {
      const existing = Chart.getChart(voteCtx);
      if (existing) {
        existing.destroy();
      }
      new Chart(voteCtx, {
        type: 'doughnut',
        data: {
          labels: nominees.map((nominee) => nominee.name),
          datasets: [{
            data: nominees.map((nominee) => nominee.votes),
            backgroundColor: ['#191bdf', '#fe6807', '#b8b9f5', '#09080d', '#cdcdd1'],
            borderWidth: 0,
            hoverOffset: 4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '80%',
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#09080d',
              padding: 10,
              titleFont: { family: "'DM Sans', sans-serif", size: 12, weight: '700' },
              bodyFont: { family: "'DM Sans', sans-serif", size: 11 },
              cornerRadius: 8,
              callbacks: {
                label: function (context) {
                  return ` ${context.parsed} votes (${context.label})`;
                }
              }
            }
          }
        }
      });
    });
  }

  function initAnalyticsCharts() {
    const data = readJson('dashboardAnalyticsChartData', null);
    if (!data) return;
    bootWhenReady('weeklySalesTrendChart', function (trendCtx) {
      const existing = Chart.getChart(trendCtx);
      if (existing) {
        existing.destroy();
      }
      new Chart(trendCtx, {
        type: 'line',
        data: {
          labels: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
          datasets: [{
            label: 'Votes Cast',
            data: data.weekly_votes || [],
            borderColor: '#191bdf',
            backgroundColor: 'rgba(25, 27, 223, 0.05)',
            borderWidth: 3.5,
            fill: true,
            tension: 0.4,
            pointRadius: 4,
            pointBackgroundColor: '#191bdf'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: '#f0f2ff', drawBorder: false },
              ticks: { font: { family: "'DM Sans', sans-serif", size: 10, weight: '600' } }
            },
            x: {
              grid: { display: false },
              ticks: { font: { family: "'DM Sans', sans-serif", size: 10, weight: '600' } }
            }
          }
        }
      });
    });
  }

  function initDashboardCharts() {
    initHomeCharts();
    initAnalyticsCharts();
  }

  window.vcInitDashboardCharts = initDashboardCharts;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDashboardCharts);
  } else {
    initDashboardCharts();
  }
  document.addEventListener('htmx:afterSwap', () => {
    window.requestAnimationFrame(() => {
      window.setTimeout(initDashboardCharts, 100);
    });
  });
  document.addEventListener('vc:lazy-section-loaded', () => {
    window.requestAnimationFrame(() => {
      window.setTimeout(initDashboardCharts, 100);
    });
  });
})();
