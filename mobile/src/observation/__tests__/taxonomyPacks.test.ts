import type { TaxonPackManifest } from "@/src/api/taxa";
import { validatePack } from "@/src/observation/taxonomyPacks";

const manifest: TaxonPackManifest = {
  pack_id: "us-midwest",
  version: "2026.07.09.1",
  scope: "country_region",
  checksum_sha256: "a".repeat(64),
  size_bytes: 200,
  taxon_count: 1,
  download_url: "https://storage.test/pack.json?sas=redacted",
  expires_at: "2026-07-09T22:00:00Z",
};

const taxon = {
  taxon_id: 12345,
  scientific_name: "Cardinalis cardinalis",
  common_name: "Northern Cardinal",
  iconic_taxon: "Aves",
  rank: "species",
  ancestor_ids: [3],
  aliases: ["cardinal"],
};

test("validates a downloaded pack against its signed manifest", () => {
  const pack = validatePack(
    {
      pack_id: manifest.pack_id,
      version: manifest.version,
      scope: manifest.scope,
      taxa: [taxon],
    },
    manifest,
  );

  expect(pack.taxa[0].taxon_id).toBe(12345);
  expect(pack.taxa[0].catalog_version).toBe(manifest.version);
});

test("rejects a pack whose version differs from the manifest", () => {
  expect(() =>
    validatePack(
      {
        pack_id: manifest.pack_id,
        version: "tampered",
        scope: manifest.scope,
        taxa: [taxon],
      },
      manifest,
    ),
  ).toThrow("does not match");
});

test("rejects duplicate taxon identifiers", () => {
  expect(() =>
    validatePack(
      {
        pack_id: manifest.pack_id,
        version: manifest.version,
        scope: manifest.scope,
        taxa: [taxon, taxon],
      },
      { ...manifest, taxon_count: 2 },
    ),
  ).toThrow("duplicate");
});
