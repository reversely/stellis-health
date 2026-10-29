/* Compare My Notes Markdown while ignoring the task IDs the server writes (ADR 0059). */
window.StellisNotesMerge = (() => {
  // Same pattern as _ID_SUFFIX in todos.py: ` ^t` plus six base-36 characters at a line end.
  const TASK_ID_SUFFIX = / \^t[0-9a-z]{6}(?=[ \t]*\r?$)/gm;
  const stripTaskIds = markdown => String(markdown ?? '').replace(TASK_ID_SUFFIX, '');
  const sameIgnoringIds = (a, b) => stripTaskIds(a) === stripTaskIds(b);

  /* Decide what the editor should hold when a local text meets the server's stored text.
     adopt: the texts differ only by task IDs, so take the server's text and drop the local one.
     keep: the local text already contains the server's text, so keep it and save it.
     combine: the texts diverged, so put the server's text first and the local text after a rule. */
  function mergeWithRemote(remote, local, sameRevision = false) {
    if (sameIgnoringIds(remote, local)) return {action:'adopt', markdown:remote};
    if (sameRevision || stripTaskIds(local).includes(stripTaskIds(remote))) return {action:'keep', markdown:local};
    return {action:'combine', markdown:`${remote}\n\n---\n\n${local}`};
  }
  return {stripTaskIds, sameIgnoringIds, mergeWithRemote};
})();
