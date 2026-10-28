export type Role = "owner" | "administrator" | "agent";
export type Organization = {
  id: string;
  name: string;
  slug: string;
  role: Role;
};
export type Member = {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
};
export type CurrentUser = {
  user: { id: string; email: string; full_name: string };
  organizations: Organization[];
  selected_organization_id: string | null;
  expires_at: string;
  absolute_expires_at: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: { current_version?: number },
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    const response = await fetch("/api/auth/csrf", {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error("Unable to establish a secure request. Please retry.");
    const csrf: { csrf_token: string } = await response.json();
    headers["X-CSRF-Token"] = csrf.csrf_token;
    headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error("Unable to connect. Check your connection and try again.");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 && payload.error?.code === "unauthenticated")
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      response.status,
      payload.error?.code ?? "request_failed",
      payload.error?.message ?? "Request failed. Please retry.",
      payload.error?.details,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
