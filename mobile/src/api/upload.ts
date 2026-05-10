/**
 * PUT a local file to a V4 signed URL with a content-type binding.
 *
 * GCS rejects PUTs whose `Content-Type` header doesn't match the value
 * baked into the signed URL when it was generated. Since the presign
 * endpoint always uses `image/jpeg`, that's what we send here.
 */
export async function putPhotoToSignedUrl(
  signedUrl: string,
  localUri: string,
): Promise<void> {
  const fileResponse = await fetch(localUri);
  const blob = await fileResponse.blob();

  const res = await fetch(signedUrl, {
    method: "PUT",
    headers: { "Content-Type": "image/jpeg" },
    body: blob,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Photo upload failed (HTTP ${res.status}): ${text.slice(0, 200) || "no body"}`,
    );
  }
}
