import type { PhotoPresignResponse } from "@/src/api/observations";
import {
  createPayload,
  resolveUploadHeaders,
} from "@/src/observation/queueTypes";

const basePresign: PhotoPresignResponse = {
  photo_id: "photo-1",
  upload_url: "https://photos.invalid",
  object_name: "pending/uploads/photo-1.jpg",
  bucket: "photos",
  content_type: "image/jpeg",
  expires_at: "2026-07-09T12:15:00.000Z",
};

describe("durable observation queue contract", () => {
  it("prefers the new upload_headers response", () => {
    expect(
      resolveUploadHeaders({
        ...basePresign,
        upload_headers: { "x-ms-blob-type": "BlockBlob", "x-new": "1" },
        required_headers: { "x-old": "1" },
      }),
    ).toEqual({ "x-ms-blob-type": "BlockBlob", "x-new": "1" });
  });

  it("accepts required_headers during the compatibility release", () => {
    expect(
      resolveUploadHeaders({
        ...basePresign,
        required_headers: { "x-ms-blob-type": "BlockBlob" },
      }),
    ).toEqual({ "x-ms-blob-type": "BlockBlob" });
  });

  it("keeps ecology tags, observed time, and coarse location in replay payloads", () => {
    const record = {
      photoId: "photo-1",
      observedAt: "2026-07-09T12:00:00.000Z",
      geohash4: "dr5r",
      locationSource: "device_coarse",
      identification: { source: "unknown", taxonId: null, speciesName: null },
      placeName: "Broad area",
      ecologyTags: { life_stage: "flowering" },
    } satisfies Parameters<typeof createPayload>[0];

    expect(createPayload(record)).toEqual(
      expect.objectContaining({
        photo_id: "photo-1",
        observed_at: "2026-07-09T12:00:00.000Z",
        geohash4: "dr5r",
        location_source: "device_coarse",
        ecology_tags: { life_stage: "flowering" },
      }),
    );
  });
});
