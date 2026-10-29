/** Keeps keyboard-triggered transitions immediate. */
(() => {
  const root = document.documentElement;
  const setInput = (input) => {
    if (root.dataset.input !== input) root.dataset.input = input;
  };
  document.addEventListener('keydown', (event) => {
    if (!['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) {
      setInput('keyboard');
    }
  }, {capture: true});
  document.addEventListener('pointerdown', () => setInput('pointer'),
                            {capture: true, passive: true});
})();
