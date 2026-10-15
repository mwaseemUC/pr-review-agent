const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelative(isoTimestamp, now = Date.now()) {
  const seconds = Math.round((new Date(isoTimestamp).getTime() - now) / 1000);
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return RELATIVE.format(Math.round(seconds / size), unit);
    }
  }
  return RELATIVE.format(seconds, "second");
}

export async function fetchComments(postId) {
  const response = await fetch(`/api/posts/${encodeURIComponent(postId)}/comments`);
  if (!response.ok) {
    throw new Error(`failed to load comments: ${response.status}`);
  }
  return response.json();
}

export function renderComments(container, comments) {
  container.replaceChildren();
  if (comments.length === 0) {
    const empty = document.createElement("p");
    empty.className = "comments-empty";
    empty.textContent = "No comments yet.";
    container.append(empty);
    return;
  }
  const list = document.createElement("ul");
  list.className = "comment-list";
  for (const comment of comments) {
    const item = document.createElement("li");
    item.className = "comment";
    item.innerHTML = `
      <span class="comment-author">${comment.authorName}</span>
      <time datetime="${comment.createdAt}">${formatRelative(comment.createdAt)}</time>
      <p class="comment-body">${comment.body}</p>`;
    list.append(item);
  }
  container.append(list);
}

export async function loadComments(container, postId) {
  container.setAttribute("aria-busy", "true");
  try {
    renderComments(container, await fetchComments(postId));
  } finally {
    container.removeAttribute("aria-busy");
  }
}
