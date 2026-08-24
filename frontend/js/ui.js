let timer = null;

export function toast(message, isError = false) {
  const node = document.getElementById('toast');
  if (!node) return;

  node.textContent = message;
  node.classList.toggle('is-error', Boolean(isError));
  node.hidden = false;

  clearTimeout(timer);
  timer = setTimeout(() => {
    node.hidden = true;
  }, isError ? 5000 : 2600);
}
