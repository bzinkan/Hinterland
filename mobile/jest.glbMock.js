/**
 * Jest stand-in for Metro .glb asset modules. Metro resolves a bundled
 * asset require() to a numeric module id; tests only need the type to
 * match (no GL runs under jest).
 */
module.exports = 1;
