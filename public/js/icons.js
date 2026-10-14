/* Render trusted Font Awesome markers while keeping surrounding API/user text as text. */
export function setIconText(element, value) {
  if (!element) return;
  const text = String(value ?? '');
  const pattern = /<i class="fa-solid (fa-[a-z0-9-]+)" aria-hidden="true"><\/i>/g;
  const fragment = document.createDocumentFragment();
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    fragment.append(document.createTextNode(text.slice(offset, match.index)));
    const icon = document.createElement('i');
    icon.className = `fa-solid ${match[1]}`;
    icon.setAttribute('aria-hidden', 'true');
    fragment.append(icon);
    offset = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(text.slice(offset)));
  element.replaceChildren(fragment);
}
