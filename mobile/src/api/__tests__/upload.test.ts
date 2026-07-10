jest.mock("expo-file-system/legacy", () => ({
  FileSystemUploadType: { BINARY_CONTENT: 0 },
  createUploadTask: jest.fn(),
}));

import * as FileSystem from "expo-file-system/legacy";

import {
  PhotoUploadError,
  putPhotoToSignedUrl,
} from "@/src/api/upload";

const createUploadTask = jest.mocked(FileSystem.createUploadTask);

describe("native observation upload", () => {
  beforeEach(() => jest.clearAllMocks());

  it("applies API-issued Azure headers verbatim", async () => {
    const uploadAsync = jest.fn(async () => ({
      status: 201,
      body: "",
      headers: {},
    }));
    createUploadTask.mockReturnValue({
      uploadAsync,
      cancelAsync: jest.fn(),
    } as never);
    const headers = {
      "Content-Type": "image/jpeg",
      "x-ms-blob-type": "BlockBlob",
    };

    await putPhotoToSignedUrl(
      "https://example.blob.core.windows.net/pending/photo.jpg",
      "file:///photo.jpg",
      headers,
    );

    expect(createUploadTask).toHaveBeenCalledWith(
      "https://example.blob.core.windows.net/pending/photo.jpg",
      "file:///photo.jpg",
      expect.objectContaining({ headers }),
    );
  });

  it("surfaces non-success storage responses", async () => {
    createUploadTask.mockReturnValue({
      uploadAsync: jest.fn(async () => ({ status: 403, body: "expired", headers: {} })),
      cancelAsync: jest.fn(),
    } as never);

    await expect(
      putPhotoToSignedUrl("https://photos.invalid", "file:///photo.jpg", {}),
    ).rejects.toBeInstanceOf(PhotoUploadError);
  });

  it("cancels the native upload task when the owner changes", async () => {
    let resolveUpload: ((value: undefined) => void) | undefined;
    const uploadAsync = jest.fn(
      () => new Promise<undefined>((resolve) => { resolveUpload = resolve; }),
    );
    const cancelAsync = jest.fn(async () => undefined);
    createUploadTask.mockReturnValue({ uploadAsync, cancelAsync } as never);
    const controller = new AbortController();

    const promise = putPhotoToSignedUrl(
      "https://photos.invalid",
      "file:///photo.jpg",
      {},
      controller.signal,
    );
    controller.abort();
    resolveUpload?.(undefined);

    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelAsync).toHaveBeenCalledTimes(1);
  });
});
