import * as Crypto from "expo-crypto";
import { Platform } from "react-native";
import * as SQLite from "expo-sqlite";

import {
  getTaxonPackManifest,
  type TaxonCatalogItem,
  type TaxonPackManifest,
} from "@/src/api/taxa";

const DATABASE_NAME = "hinterland-taxonomy.db";
const MAX_PACK_BYTES = 25_000_000;

type PackTaxon = TaxonCatalogItem & {
  aliases: string[];
  ancestor_ids: number[];
};

type DownloadedPack = {
  pack_id: string;
  version: string;
  scope: string;
  taxa: PackTaxon[];
};

type TaxonRow = {
  taxon_id: number;
  scientific_name: string | null;
  common_name: string | null;
  iconic_taxon: string | null;
  rank: string | null;
  ancestor_ids_json: string;
  catalog_version: string;
};

let databasePromise: Promise<SQLite.SQLiteDatabase> | null = null;

async function getDatabase(): Promise<SQLite.SQLiteDatabase> {
  if (Platform.OS === "web") {
    throw new Error("Downloadable taxonomy packs are available on iOS and Android only.");
  }
  if (!databasePromise) {
    databasePromise = SQLite.openDatabaseAsync(DATABASE_NAME).then(async (database) => {
      await database.execAsync(`
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS taxonomy_packs (
          pack_id TEXT PRIMARY KEY NOT NULL,
          version TEXT NOT NULL,
          scope TEXT NOT NULL,
          checksum_sha256 TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          installed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS taxonomy_pack_taxa (
          pack_id TEXT NOT NULL,
          taxon_id INTEGER NOT NULL,
          scientific_name TEXT,
          common_name TEXT,
          iconic_taxon TEXT,
          rank TEXT,
          ancestor_ids_json TEXT NOT NULL,
          aliases_json TEXT NOT NULL,
          catalog_version TEXT NOT NULL,
          PRIMARY KEY (pack_id, taxon_id),
          FOREIGN KEY (pack_id) REFERENCES taxonomy_packs(pack_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_taxonomy_pack_taxa_names
          ON taxonomy_pack_taxa (common_name, scientific_name);
      `);
      return database;
    });
  }
  return databasePromise;
}

/** Download, hash-verify, validate, and atomically replace one immutable pack. */
export async function installTaxonPack(packId: string): Promise<TaxonPackManifest> {
  const manifest = await getTaxonPackManifest(packId);
  if (manifest.size_bytes <= 0 || manifest.size_bytes > MAX_PACK_BYTES) {
    throw new Error("This organism pack has an unsupported size.");
  }
  const response = await fetch(manifest.download_url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Organism pack download failed (${response.status}).`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== manifest.size_bytes) {
    throw new Error("The organism pack size did not match its manifest.");
  }
  const digest = toHex(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes));
  if (digest !== manifest.checksum_sha256.toLowerCase()) {
    throw new Error("The organism pack checksum did not match its manifest.");
  }

  const pack = validatePack(JSON.parse(new TextDecoder().decode(bytes)), manifest);
  const database = await getDatabase();
  await database.withTransactionAsync(async () => {
    await database.runAsync(`DELETE FROM taxonomy_packs WHERE pack_id = ?`, pack.pack_id);
    await database.runAsync(
      `INSERT INTO taxonomy_packs
       (pack_id, version, scope, checksum_sha256, size_bytes, installed_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      pack.pack_id,
      pack.version,
      pack.scope,
      manifest.checksum_sha256,
      manifest.size_bytes,
      new Date().toISOString(),
    );
    for (const taxon of pack.taxa) {
      await database.runAsync(
        `INSERT INTO taxonomy_pack_taxa
         (pack_id, taxon_id, scientific_name, common_name, iconic_taxon, rank,
          ancestor_ids_json, aliases_json, catalog_version)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        pack.pack_id,
        taxon.taxon_id,
        taxon.scientific_name,
        taxon.common_name,
        taxon.iconic_taxon,
        taxon.rank ?? null,
        JSON.stringify(taxon.ancestor_ids),
        JSON.stringify(taxon.aliases),
        pack.version,
      );
    }
  });
  return manifest;
}

export async function searchInstalledTaxa(
  query: string,
  limit = 20,
): Promise<TaxonCatalogItem[]> {
  const normalized = query.trim().toLocaleLowerCase();
  if (normalized.length < 2 || Platform.OS === "web") return [];
  const database = await getDatabase();
  const pattern = `%${normalized}%`;
  const rows = await database.getAllAsync<TaxonRow>(
    `SELECT DISTINCT taxon_id, scientific_name, common_name, iconic_taxon, rank,
            ancestor_ids_json, catalog_version
       FROM taxonomy_pack_taxa
      WHERE lower(coalesce(common_name, '')) LIKE ?
         OR lower(coalesce(scientific_name, '')) LIKE ?
         OR lower(aliases_json) LIKE ?
      ORDER BY common_name, scientific_name, taxon_id
      LIMIT ?`,
    pattern,
    pattern,
    pattern,
    Math.max(1, Math.min(limit, 20)),
  );
  return rows.map((row) => ({
    taxon_id: row.taxon_id,
    scientific_name: row.scientific_name,
    common_name: row.common_name,
    iconic_taxon: row.iconic_taxon,
    rank: row.rank,
    ancestor_ids: parseIntegerArray(row.ancestor_ids_json),
    catalog_version: row.catalog_version,
  }));
}

export function validatePack(value: unknown, manifest: TaxonPackManifest): DownloadedPack {
  if (!isRecord(value) || !Array.isArray(value.taxa)) {
    throw new Error("The organism pack payload is malformed.");
  }
  if (
    value.pack_id !== manifest.pack_id ||
    value.version !== manifest.version ||
    value.scope !== manifest.scope ||
    value.taxa.length !== manifest.taxon_count
  ) {
    throw new Error("The organism pack payload does not match its manifest.");
  }
  const seen = new Set<number>();
  const taxa = value.taxa.map((item) => {
    if (
      !isRecord(item) ||
      !Number.isInteger(item.taxon_id) ||
      Number(item.taxon_id) <= 0 ||
      typeof item.scientific_name !== "string" ||
      !Array.isArray(item.aliases) ||
      !item.aliases.every((alias) => typeof alias === "string") ||
      !Array.isArray(item.ancestor_ids) ||
      !item.ancestor_ids.every(Number.isInteger)
    ) {
      throw new Error("The organism pack contains a malformed taxon.");
    }
    const taxonId = Number(item.taxon_id);
    if (seen.has(taxonId)) throw new Error("The organism pack contains duplicate taxa.");
    seen.add(taxonId);
    return {
      taxon_id: taxonId,
      scientific_name: item.scientific_name,
      common_name: typeof item.common_name === "string" ? item.common_name : null,
      iconic_taxon: typeof item.iconic_taxon === "string" ? item.iconic_taxon : null,
      rank: typeof item.rank === "string" ? item.rank : null,
      ancestor_ids: item.ancestor_ids.map(Number),
      aliases: item.aliases,
      catalog_version: manifest.version,
    };
  });
  return {
    pack_id: manifest.pack_id,
    version: manifest.version,
    scope: manifest.scope,
    taxa,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseIntegerArray(value: string): number[] {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter(Number.isInteger).map(Number) : [];
  } catch {
    return [];
  }
}

function toHex(value: ArrayBuffer): string {
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}
