import "server-only";
import { type RequestOptions, type Service, serviceRequest, serviceUrl } from "@/lib/service-api";

export type { ApiError, ApiResult } from "@/lib/service-api";

const IDENTITY: Service = {
  baseUrl: () => serviceUrl("IDENTITY_SERVICE_URL", "http://localhost:8001"),
  unreachable: "The identity service didn't respond.",
};

/** Calls identity-service as the signed-in user, from the server only (see `serviceRequest`). */
export function identityRequest<T>(path: string, options?: RequestOptions) {
  return serviceRequest<T>(IDENTITY, path, options);
}
