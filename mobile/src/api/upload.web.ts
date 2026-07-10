/** Web adult-console shim: child photo upload is native-only. */
export class PhotoUploadError extends Error {
  constructor(
    public readonly status: number,
    body: string,
  ) {
    super(`Photo upload failed (HTTP ${status}): ${body}`);
    this.name = "PhotoUploadError";
  }
}

export { PhotoUploadError as UploadHttpError };

export async function putPhotoToSignedUrl(
  _signedUrl: string,
  _localUri: string,
  _headers: Readonly<Record<string, string>>,
  _signal?: AbortSignal,
): Promise<void> {
  throw new Error("Observation photo upload is available on iOS and Android only.");
}

export function legacyPutHeaders(contentType: string): Record<string, string> {
  return {
    "Content-Type": contentType,
    "x-ms-blob-type": "BlockBlob",
  };
}
