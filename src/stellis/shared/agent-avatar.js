// Generated pixel avatars for agents (#387). A seed, normally the agent ID, picks a colour and a 7 by 7 sprite mirrored
// about its middle column, drawn as one SVG path. Coordinators (the default agent and notebook stewards) draw a
// white sprite on a filled disc; acting agents draw a coloured sprite on a white disc with a hairline ring.
(function (root) {
  const COLORS = ['#335C63', '#547980', '#495575', '#56465C', '#654654', '#4F6B4A', '#8A5A2B', '#7A5C8A'];

  // FNV-1a over the seed, then xorshift for the sprite's bits, so a seed always draws the same avatar.
  function hash(seed) {
    let value = 2166136261;
    for (const char of String(seed || 'agent')) { value ^= char.charCodeAt(0); value = Math.imul(value, 16777619) >>> 0; }
    return value;
  }

  function spritePath(seed) {
    let state = hash(seed) || 1;
    const bit = () => { state ^= state << 13; state >>>= 0; state ^= state >>> 17; state ^= state << 5; state >>>= 0; return state & 1; };
    const cells = new Set();
    const set = (x, y) => { cells.add(`${x},${y}`); cells.add(`${6 - x},${y}`); };
    for (let y = 2; y <= 5; y++) for (let x = 1; x <= 3; x++) set(x, y);
    if (bit()) { set(0, 2); set(0, 3); set(0, 4); } else set(0, 4);
    if (bit()) { set(1, 1); set(1, 0); } else { set(2, 1); set(2, 0); }
    if (bit()) set(1, 6); else { set(1, 6); set(2, 6); }
    if (bit()) set(3, 1);
    cells.delete('2,3'); cells.delete('4,3');
    if (bit()) cells.delete('3,5');
    return [...cells].sort().map(key => { const [x, y] = key.split(','); return `M${x} ${y}h1v1h-1z`; }).join('');
  }

  function colorFor(seed) { return COLORS[hash(`${seed}:color`) % COLORS.length]; }

  // An agent record from GET /agents coordinates when it is the default agent or stewards a notebook.
  function isCoordinator(agent) {
    return Boolean(agent && (agent.is_default || (agent.notebooks || []).some(item => item.is_steward)));
  }

  // A decorative avatar: the name always sits beside it, so it is hidden from assistive technology.
  function avatarElement(doc, {seed, coordinator = false, size = 24} = {}) {
    const color = colorFor(seed);
    const span = doc.createElement('span');
    span.className = `agent-avatar ${coordinator ? 'agent-avatar-coordinator' : 'agent-avatar-acting'}`;
    span.setAttribute('aria-hidden', 'true');
    span.style.setProperty('--avatar-size', `${size}px`);
    span.style.setProperty('--avatar-color', color);
    const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '-0.5 -0.5 8 8');
    svg.setAttribute('shape-rendering', 'crispEdges');
    const path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', spritePath(seed));
    svg.append(path);
    span.append(svg);
    return span;
  }

  const api = {spritePath, colorFor, isCoordinator, avatarElement, AVATAR_COLORS: COLORS};
  root.StellisAgentAvatar = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
