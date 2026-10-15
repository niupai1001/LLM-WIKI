(() => {
  const library = document.querySelector('meta[name="library-id"]')?.content;
  if (!library) return;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || url.pathname.startsWith('/static/')) return originalFetch(input, options);
    const headers = new Headers(input instanceof Request ? input.headers : options.headers);
    if (!headers.has('X-Library-ID')) headers.set('X-Library-ID', library);
    return originalFetch(input, {...options, headers});
  };
})();
