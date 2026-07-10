/**
 * Binary upload to an API-issued object-storage URL.
 *
 * FileSystem's native upload task avoids the unreliable Hermes
 * fetch(file://).blob() path and can be cancelled when the active account
 * changes. Required headers are supplied by the API and sent verbatim.
 */
import * as FileSystem from "expo-file-system/legacy";

export class PhotoUploadError extends Error {
  constructor(
    public readonly status: number,
    body: string,
  ) {
    super(
      `Photo upload failed (HTTP ${status}): ${body.slice(0, 200) || "no body"}`,
    );
    this.name = "PhotoUploadError";
  }
}

/** Backward-compatible name used by existing upload callers/tests. */
export { PhotoUploadError as UploadHttpError };

export async function putPhotoToSignedUrl(
  signedUrl: string,
  localUri: string,
  headers: Readonly<Record<string, string>>,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) throw abortError();

  const task = FileSystem.createUploadTask(signedUrl, localUri, {
    httpMethod: "PUT",
    uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
    headers: { ...headers },
  });
  const cancel = () => {
    void task.cancelAsync();
  };
  signal?.addEventListener("abort", cancel, { once: true });

  try {
    const result = await task.uploadAsync();
    if (signal?.aborted || !result) throw abortError();
    if (result.status < 200 || result.status >= 300) {
      throw new PhotoUploadError(result.status, result.body);
    }
  } finally {
    signal?.removeEventListener("abort", cancel);
  }
}

/** One-release fallback for an API that predates returned upload headers. */
export function legacyPutHeaders(contentType: string): Record<string, string> {
  return {
    "Content-Type": contentType,
    "x-ms-blob-type": "BlockBlob",
  };
}

function abortError(): Error {
  const error = new Error("Photo upload cancelled");
  error.name = "AbortError";
  return error;
}
