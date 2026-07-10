import {
  CORE_TAXA_CHECKSUM_SHA256,
  CORE_TAXA_COUNT,
  CORE_TAXA_VERSION,
  mergeTaxonResults,
  searchCoreTaxa,
} from "@/src/observation/coreTaxa";

describe("bundled core taxonomy", () => {
  it("finds a canonical higher taxon while offline", () => {
    expect(CORE_TAXA_VERSION).toBe("2026.07.09.3");
    expect(CORE_TAXA_COUNT).toBe(45);
    expect(CORE_TAXA_CHECKSUM_SHA256).toBe(
      "dec29f8887f7f7d3d960db4f90f55be4fa90eb4fdf2b5a8ed53a89df658605df",
    );
    expect(searchCoreTaxa("spider")[0]).toMatchObject({
      taxon_id: 47119,
      scientific_name: "Arachnida",
    });
    expect(searchCoreTaxa("redbird")[0]).toMatchObject({
      taxon_id: 9083,
      scientific_name: "Cardinalis cardinalis",
    });
    expect(searchCoreTaxa("garlic mustard")[0]).toMatchObject({
      taxon_id: 56061,
    });
  });

  it("deduplicates server results by canonical taxon id", () => {
    const local = searchCoreTaxa("bird");
    const duplicatedId = local[0].taxon_id;
    const merged = mergeTaxonResults(local, [
      { ...local[0], common_name: "Birds from catalog" },
    ]);
    expect(merged).toHaveLength(local.length);
    expect(merged.filter((taxon) => taxon.taxon_id === duplicatedId)).toHaveLength(1);
    expect(merged.find((taxon) => taxon.taxon_id === duplicatedId)?.common_name).toBe(
      "Birds from catalog",
    );
  });
});
