const form = document.querySelector('#auth-form');
const error = document.querySelector('#auth-error');
const signIn = document.querySelector('#sign-in-tab');
const signUp = document.querySelector('#sign-up-tab');
let registration = false;
let registrationOpen = false;
let lockedWorkspaces = false;
let rollbackAccepted = false;

/** True when the server returns an older workspace than this browser last opened; it remembers the newest. */
function olderThanSeen(username, header) {
  const version = Number(header);
  if (!username || !Number.isInteger(version) || version < 1) return false;
  const key = 'stellis-workspace-version:' + username.trim().toLowerCase();
  try {
    const seen = Number(localStorage.getItem(key) || 0);
    if (version < seen) return true;
    localStorage.setItem(key, String(version));
  } catch { /* Private windows may refuse storage; the check then has nothing to compare. */ }
  return false;
}
let inviteRequired = false;

function showMode(createAccount) {
  registration = createAccount && registrationOpen;
  createAccount = registration;
  signIn.setAttribute('aria-current', createAccount ? 'false' : 'page');
  signUp.setAttribute('aria-current', createAccount ? 'page' : 'false');
  document.querySelector('#auth-title').textContent = createAccount ? 'Create account' : 'Sign in';
  document.querySelector('#auth-submit').textContent = createAccount ? 'Create account' : 'Sign in';
  document.querySelector('#username').minLength = createAccount ? 3 : 1;
  document.querySelector('#password').minLength = createAccount ? 12 : 1;
  document.querySelector('#password').autocomplete = createAccount ? 'new-password' : 'current-password';
  const invite = document.querySelector('#invite-code');
  invite.hidden = invite.labels[0].hidden = !(createAccount && inviteRequired);
  invite.required = createAccount && inviteRequired;
  for (const id of ['#keep-debug-label', '#keep-debug-hint'])
    document.querySelector(id).hidden = !createAccount;
  document.querySelector('#encryption-hint').hidden = !(createAccount && lockedWorkspaces);
  document.querySelector('#forgot-password').hidden = createAccount || !lockedWorkspaces;
  error.textContent = '';
}

signIn.addEventListener('click', () => showMode(false));

/** Where to go after signing in: the page that asked, when it is on this site, else Home or setup. */
function destination(onboardingRequired) {
  const next = new URLSearchParams(location.search).get('next') || '';
  if (next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/auth')) return next;
  return onboardingRequired ? '/onboarding' : '/';
}
signUp.addEventListener('click', () => showMode(true));

(async () => {
  try {
    const response = await fetch('/api/v1/auth/status');
    if (!response.ok) throw new Error();
    const status = await response.json();
    const creating = new URLSearchParams(location.search).get('create') === '1';
    // A demo guest who opens the main page from the app stays on it, with a way back to the demo.
    const visiting = status.guest && new URLSearchParams(location.search).get('home') === '1';
    if (!status.required || (status.username && !creating && !visiting)) { location.replace(destination(status.onboarding_required)); return; }
    if (visiting) {
      const back = document.querySelector('#take-demo');
      document.querySelector('#demo-entry').hidden = false;
      back.textContent = 'Back to the demo';
      back.addEventListener('click', event => { event.stopImmediatePropagation(); location.assign('/'); }, {capture: true});
      document.querySelector('.auth-card').classList.add('is-ready');
      return;
    }
    registrationOpen = status.registration_open;
    inviteRequired = status.invite_required;
    lockedWorkspaces = Boolean(status.locked_workspaces);
    signUp.hidden = !registrationOpen;
    document.querySelector('#demo-entry').hidden = !status.demo_available;
    document.querySelector('.auth-modes').hidden = false;
    showMode(creating || !status.has_accounts);
  } catch { error.textContent = 'Could not load account status.'; }
  finally { document.querySelector('.auth-card').classList.add('is-ready'); }
})();

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (registration && !registrationOpen) return;
  error.textContent = '';
  const button = document.querySelector('#auth-submit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/v1/auth/${registration ? 'register' : 'login'}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.querySelector('#username').value,
        password: document.querySelector('#password').value,
        ...(registration && inviteRequired ? {invite_code: document.querySelector('#invite-code').value} : {}),
        ...(registration ? {keep_debug_data: document.querySelector('#keep-debug').checked} : {})}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Sign in failed');
    if (olderThanSeen(result.username, response.headers.get('X-Stellis-Workspace-Version')) && !rollbackAccepted) {
      rollbackAccepted = true;
      button.textContent = 'Continue anyway';
      throw new Error('These records are older than the version this browser last opened');
    }
    const next = destination(result.onboarding_required);
    if (result.recovery_code) showRecoveryCode(result.recovery_code, next);
    else location.replace(next);
  } catch (problem) { error.textContent = problem.message; }
  finally { button.disabled = false; }
});

/** The recovery key appears once; Continue waits until the person says it is saved. */
function showRecoveryCode(code, next) {
  const panel = document.querySelector('#recovery-panel');
  // The key belongs to the new account; the demo offer is for people who haven't signed in.
  document.querySelector('#demo-entry').hidden = true;
  for (const hidden of [form, document.querySelector('.auth-modes'), document.querySelector('#auth-title'),
    document.querySelector('#forgot-password')]) hidden.hidden = true;
  document.querySelector('#recovery-code').value = code;
  panel.hidden = false;
  const saved = document.querySelector('#recovery-saved');
  const proceed = document.querySelector('#recovery-continue');
  saved.addEventListener('change', () => { proceed.disabled = !saved.checked; });
  document.querySelector('#recovery-copy').addEventListener('click', async event => {
    try { await navigator.clipboard.writeText(code); event.currentTarget.textContent = 'Copied'; }
    catch { error.textContent = 'Copy failed. Select the key and copy it'; }
  });
  document.querySelector('#recovery-download').addEventListener('click', () => {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([`Stellis Health recovery key\n${code}\n`], {type: 'text/plain'}));
    link.download = 'stellis-recovery-key.txt';
    link.click();
    URL.revokeObjectURL(link.href);
  });
  panel.addEventListener('submit', event => { event.preventDefault(); location.replace(next); });
  document.querySelector('#recovery-code').focus?.();
}

const recoverForm = document.querySelector('#recover-form');
function showRecover(open) {
  recoverForm.hidden = !open;
  form.hidden = open;
  document.querySelector('.auth-modes').hidden = open;
  document.querySelector('#forgot-password').hidden = open || registration || !lockedWorkspaces;
  document.querySelector('#auth-title').textContent = open ? 'Reset password' : (registration ? 'Create account' : 'Sign in');
  error.textContent = '';
  if (open) document.querySelector('#recover-username').value = document.querySelector('#username').value;
}
document.querySelector('#forgot-password').addEventListener('click', () => showRecover(true));
document.querySelector('#recover-back').addEventListener('click', () => showRecover(false));
recoverForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.querySelector('#recover-submit');
  button.disabled = true;
  error.textContent = '';
  try {
    const response = await fetch('/api/v1/auth/recover', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.querySelector('#recover-username').value,
        recovery_key: document.querySelector('#recovery-key').value,
        new_password: document.querySelector('#recover-password').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Reset failed');
    location.replace(destination(result.onboarding_required));
  } catch (problem) { error.textContent = problem.message; }
  finally { button.disabled = false; }
});

// The demo (#247) replaces sign-up and setup: a guest account with an empty example workspace, then the
// walkthrough, where the visitor adds Maya's records the way they would add their own (#258).
// The demo button sits in the page's opening, outside the account card, so its errors show beside it.
const demoError = document.querySelector('#demo-error') || error;
document.querySelector('#take-demo').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = 'Preparing the demo';
  demoError.textContent = '';
  try {
    const guest = await fetch('/api/v1/auth/demo', {method: 'POST'});
    if (!guest.ok) throw new Error((await guest.json().catch(() => ({}))).detail || 'The demo could not start. Try again.');
    const entered = await fetch('/api/v1/demo/enter', {method: 'POST'});
    if (!entered.ok) throw new Error('The demo could not start. Try again.');
    try {
      for (const key of ['stellis-demo-met', 'stellis-demo-small', 'stellis-demo-added', 'stellis-demo-recording']) sessionStorage.removeItem(key);
      sessionStorage.setItem('stellis-demo-step', '0');
    } catch {}
    location.assign('/');
  } catch (failure) {
    demoError.textContent = failure.message;
    button.disabled = false;
    button.textContent = 'Try the demo';
  }
});
