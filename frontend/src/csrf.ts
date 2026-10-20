/** The CSRF token the server set in a readable cookie at sign-in. */
export function csrfToken(): string {
  const prefix = "smart_learning_csrf=";
  const value = document.cookie.split("; ").find((entry) => entry.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}
