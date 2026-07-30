const STORAGE_KEY = "hinterland.parent_return_path.v1";

export type ParentReturnPath = "/groups" | "/group-invite";

export function rememberParentReturnPath(path: ParentReturnPath): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, path);
  } catch {
    // The fallback route remains safe when tab storage is unavailable.
  }
}

export function consumeParentReturnPath(
  fallback: ParentReturnPath = "/groups",
): ParentReturnPath {
  if (typeof window === "undefined") return fallback;
  try {
    const value = window.sessionStorage.getItem(STORAGE_KEY);
    window.sessionStorage.removeItem(STORAGE_KEY);
    return value === "/group-invite" || value === "/groups" ? value : fallback;
  } catch {
    return fallback;
  }
}
