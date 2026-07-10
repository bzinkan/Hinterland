/** Web adult-console shim: regional pack storage is native-only. */
import type {
  TaxonCatalogItem,
  TaxonPackManifest,
} from "@/src/api/taxa";

export async function installTaxonPack(
  _packId: string,
): Promise<TaxonPackManifest> {
  throw new Error("Downloadable taxonomy packs are available on iOS and Android only.");
}

export async function searchInstalledTaxa(
  _query: string,
  _limit = 20,
): Promise<TaxonCatalogItem[]> {
  return [];
}

export function validatePack(
  _value: unknown,
  _manifest: TaxonPackManifest,
): never {
  throw new Error("Taxonomy pack validation is unavailable on web.");
}
