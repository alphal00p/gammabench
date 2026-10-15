# The website catalog is also the manifest of locally packaged benchmarks.
# Entries without a recipe remain historical or planned website content.
{
  pkgs,
  inputs,
  craneLib,
  sourceRoot,
  toolchain,
  mkBenchmark,
  infrastructure,
  gammabench,
  reservedNames ? [ ],
}:
let
  inherit (pkgs) lib;
  catalog = builtins.fromJSON (builtins.readFile (sourceRoot + "/web/benchmarks.json"));
  safePath =
    prefix: suffix: value:
    builtins.isString value
    && lib.hasPrefix prefix value
    && lib.hasSuffix suffix value
    && builtins.match "[a-zA-Z0-9][a-zA-Z0-9._/-]*" value != null
    && lib.all (part: part != "" && part != "." && part != "..") (lib.splitString "/" value);
  entries = lib.filter (entry: entry ? nix_recipe) catalog.benchmarks;
  ids = map (entry: entry.id) catalog.benchmarks;
  names = lib.concatMap (entry: [
    entry.id
    "bench-${entry.id}"
  ]) entries;
  validEntry =
    entry:
    builtins.isAttrs entry
    && entry ? id
    && builtins.isString entry.id
    && builtins.match "[a-z0-9][a-z0-9-]*" entry.id != null;
  validRecipe =
    entry:
    safePath "nix/" ".nix" entry.nix_recipe
    && entry ? suite
    && safePath "suites/" ".json" entry.suite
    && builtins.pathExists (sourceRoot + "/${entry.nix_recipe}")
    && builtins.pathExists (sourceRoot + "/${entry.suite}");
  benchmarks = lib.listToAttrs (
    map (entry: {
      name = entry.id;
      value = import (sourceRoot + "/${entry.nix_recipe}") {
        inherit
          pkgs
          inputs
          craneLib
          sourceRoot
          toolchain
          mkBenchmark
          infrastructure
          ;
      };
    }) entries
  );
in
assert lib.assertMsg (
  catalog.schema_version == 1 && builtins.isList catalog.benchmarks
) "Unsupported benchmark registry";
assert lib.assertMsg (
  lib.all validEntry catalog.benchmarks && lib.unique ids == ids
) "Benchmark registry IDs must be unique safe URL segments";
assert lib.assertMsg (lib.all validRecipe entries)
  "Registered benchmarks require existing relative nix/*.nix recipes and suites/*.json definitions";
assert lib.assertMsg (
  lib.unique names == names
  && lib.intersectLists reservedNames names == [ ]
  && lib.all (entry: !(lib.hasPrefix "bench-" entry.id)) entries
) "Registered benchmark package/app names collide with existing outputs";
{
  packages =
    (lib.mapAttrs (_: benchmark: benchmark.package) benchmarks)
    // lib.mapAttrs' (name: benchmark: lib.nameValuePair "bench-${name}" benchmark.runner) benchmarks;
  apps = lib.mapAttrs (_: benchmark: benchmark.app) benchmarks;
  checks = lib.mapAttrs' (
    name: benchmark:
    lib.nameValuePair "registered-${name}-definition" (
      pkgs.runCommand "gammabench-${name}-definition" { nativeBuildInputs = [ gammabench ]; } ''
        gammabench validate ${benchmark.package}/suite.json
        touch "$out"
      ''
    )
  ) benchmarks;
}
