import {
  K_DEPTH,
  K_HEIGHT,
  K_PERSP,
  project,
  S,
} from "@/src/sanctuary/diorama/projection";

describe("project", () => {
  it("maps the origin to the island anchor at full scale", () => {
    expect(project([0, 0, 0])).toEqual({ x: 0, y: 0, depth: 0, scaleMul: 1 });
  });

  it("matches the golden formula for an arbitrary point", () => {
    const p = project([1, 2, 3]);
    expect(p.x).toBeCloseTo(1 * S, 10);
    expect(p.y).toBeCloseTo(3 * S * K_DEPTH - 2 * S * K_HEIGHT, 10);
    expect(p.depth).toBe(3);
    expect(p.scaleMul).toBeCloseTo(1 - 3 * K_PERSP, 10);
  });

  it("golden values: [1, 2, 3] with the shipped constants", () => {
    const p = project([1, 2, 3]);
    expect(p.x).toBeCloseTo(14, 10);
    expect(p.y).toBeCloseTo(-1.4, 10);
    expect(p.scaleMul).toBeCloseTo(0.94, 10);
  });

  it("+z pushes down-screen and shrinks", () => {
    const near = project([0, 0, 2]);
    const origin = project([0, 0, 0]);
    expect(near.y).toBeGreaterThan(origin.y);
    expect(near.scaleMul).toBeLessThan(origin.scaleMul);
    expect(near.depth).toBeGreaterThan(origin.depth);
  });

  it("+y lifts up-screen without changing depth or scale", () => {
    const lifted = project([0, 1.5, 0]);
    expect(lifted.y).toBeLessThan(0);
    expect(lifted.depth).toBe(0);
    expect(lifted.scaleMul).toBe(1);
  });

  it("x maps linearly and independently", () => {
    expect(project([-2, 5, 9]).x).toBeCloseTo(-2 * S, 10);
    expect(project([2, 0, 0]).x).toBeCloseTo(2 * S, 10);
  });

  it("is deterministic", () => {
    expect(project([0.3, -0.7, 1.9])).toEqual(project([0.3, -0.7, 1.9]));
  });
});
