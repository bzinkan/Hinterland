/**
 * PUT a local file to a presigned Azure Blob SAS URL.
 *
 * The presign response carries `required_headers` -- the exact headers the
 * storage backend demands on the PUT (Azure rejects Put Blob without
 * `x-ms-blob-type: BlockBlob`). Send them verbatim; never reconstruct them
 * client-side.
 *
 * Uses `FileSystem.uploadAsync` instead of `fetch(file://...).blob()`:
 * Hermes Blob handling is unreliable for binary bodies (the same reason
 * ADR 0011 bans Blob/fetch for GLB loading) and RN networking can rewrite
 * the Content-Type on Blob bodies.
 */
import * as FileSystem from "expo-file-system/legacy";

export class UploadHttpError extends Error {
  constructor(
    public readonly status: number,
    body: string,
  ) {
    super(`Photo upload failed (HTTP ${status}): ${body.slice(0, 200) || "no body"}`);
    this.name = "UploadHttpError";
  }
}

export async function putPhotoToSignedUrl(
  signedUrl: string,
  localUri: string,
  headers: Record<string, string>,
): Promise<void> {
  const res = await FileSystem.uploadAsync(signedUrl, localUri, {
    httpMethod: "PUT",
    uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
    headers,
  });

  // uploadAsync resolves on any HTTP status; non-2xx is our failure.
  if (res.status < 200 || res.status >= 300) {
    throw new UploadHttpError(res.status, res.body);
  }
}

/**
 * Fallback for API builds that predate `required_headers` in the presign
 * response (the deployed dev API may lag this client). Mirrors the Azure
 * contract the backend returns today.
 */
export function legacyPutHeaders(contentType: string): Record<string, string> {
  return {
    "Content-Type": contentType,
    "x-ms-blob-type": "BlockBlob",
  };
}
