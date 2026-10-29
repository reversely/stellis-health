/**
 * @fileoverview Home menu entries for setup and the guided tour. The tour
 * runs as a call docked over the app (coach-call.js); finishing setup opens
 * Home with ?tour=1, which offers it.
 */
(() => {
  async function openSetup(event) {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const response =
          await fetch('/api/v1/onboarding/reopen', {method: 'POST'});
      if (!response.ok) throw Error('Setup could not open. Try again.');
      location.assign('/onboarding');
    } catch (error) {
      button.disabled = false;
      const status = document.querySelector('#status');
      status.textContent = error.message;
      status.setAttribute('role', 'alert');
    }
  }
  document.getElementById('workspace-setup')
      ?.addEventListener('click', openSetup);
  document.getElementById('take-tour')?.addEventListener('click', (event) => {
    event.currentTarget.closest('details')?.removeAttribute('open');
    window.StellisCoachCall.open({kind:'tour'});
  });
  const url = new URL(location.href);
  if (url.searchParams.get('tour') === '1') {
    url.searchParams.delete('tour');
    history.replaceState(history.state, '', url);
    window.StellisCoachCall.offerTour();
  }
})();
