const base = (
  import.meta.env.VITE_API_BASE_URL || "/api"
).replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(
  path,
  { token, method = "GET", body, idempotencyKey } = {},
) {
  const headers = { Accept: "application/json" };

  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
    cache: "no-store",
  });

  let data;

  try {
    data = await response.json();
  } catch {
    if (response.ok) {
      throw new ApiError(502, "API trả về dữ liệu không hợp lệ. Hãy thử lại yêu cầu.");
    }

    data = { detail: `HTTP ${response.status}` };
  }

  if (response.ok && (data === null || typeof data !== "object")) {
    throw new ApiError(502, "API trả về dữ liệu không hợp lệ. Hãy thử lại yêu cầu.");
  }

  if (!response.ok) {
    const detail = typeof data?.detail === "string"
      ? data.detail
      : JSON.stringify(data?.detail);

    throw new ApiError(
      response.status,
      detail || `HTTP ${response.status}`,
    );
  }

  return data;
}
