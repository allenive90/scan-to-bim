// Executed only for an explicit navigation request, never for field reruns.
(() => {
  const ticket = __SCROLL_TICKET__;
  window.scanToBimScrollCleanup?.();
  let frame;
  let deadline;
  let previousTop;
  let stableFrames = 0;
  let stopped = false;
  const cancelEvents = ["pointerdown", "wheel", "keydown", "touchstart"];
  const cleanup = () => {
    stopped = true;
    cancelAnimationFrame(frame);
    clearTimeout(deadline);
    observer.disconnect();
    cancelEvents.forEach(name => document.removeEventListener(name, cleanup, true));
    if (window.scanToBimScrollCleanup === cleanup) {
      delete window.scanToBimScrollCleanup;
    }
  };
  const attempt = () => {
    if (stopped) return;
    const anchor = document.querySelector(`[data-scroll-ticket="${ticket}"]`);
    if (anchor && anchor.getClientRects().length) {
      const top = anchor.getBoundingClientRect().top;
      stableFrames = previousTop !== undefined && Math.abs(top - previousTop) < 0.5
        ? stableFrames + 1 : 0;
      previousTop = top;
      // Wait for Streamlit's new section to be laid out before scrolling.
      if (stableFrames >= 3) {
        anchor.scrollIntoView({block: "start", behavior: "instant"});
        anchor.focus({preventScroll: true});
        cleanup();
        return;
      }
    }
    frame = requestAnimationFrame(attempt);
  };
  const observer = new MutationObserver(() => {
    previousTop = undefined;
    stableFrames = 0;
  });
  observer.observe(document.body, {childList: true, subtree: true});
  window.scanToBimScrollCleanup = cleanup;
  // Respect a user who has already started interacting with the new section.
  cancelEvents.forEach(name => document.addEventListener(name, cleanup, {capture: true, passive: true}));
  deadline = setTimeout(cleanup, 6000);
  frame = requestAnimationFrame(attempt);
})();
