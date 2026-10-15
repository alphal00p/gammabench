{
  inputs,
  system,
  sourceRoot,
  pkgs,
  craneLib,
  toolchain,
  mkBenchmark,
  infrastructure,
  external,
}:
let
  inherit (pkgs) lib;
  recipeRoot = lib.fileset.toSource {
    root = sourceRoot;
    fileset = lib.fileset.unions [
      (sourceRoot + "/suites/generation")
      (sourceRoot + "/src/gammabench/sources.py")
      (sourceRoot + "/src/gammabench/__init__.py")
      (sourceRoot + "/tools.lock.json")
    ];
  };
  sourceInfo = name: url: {
    kind = "git";
    inherit url;
    url_kind = "canonical-project";
    canonical_project_url = url;
    revision = inputs.${name}.rev;
    nar_hash = inputs.${name}.narHash;
    store_path = "${inputs.${name}}";
  };
  sources = {
    gammaloop = sourceInfo "gammaloop" "https://github.com/alphal00p/gammaloop.git";
    symbolica = sourceInfo "symbolica" "https://github.com/symbolica-dev/symbolica.git";
    feyngraph = sourceInfo "feyngraph" "https://github.com/Jens-Braun/FeynGraph.git";
  };
  pins = (builtins.fromJSON (builtins.readFile (sourceRoot + "/tools.lock.json"))).tools;
  archiveSource = name: {
    kind = "archive";
    inherit (pins.${name}) url version;
    archive_sha256 = pins.${name}.sha256;
  };
  profile = {
    name = "release";
    opt_level = 3;
    debug = 1;
    lto = "thin";
    codegen_units = 1;
  };
  helper = pkgs.writeText "generation-source-config.json" (
    builtins.toJSON {
      gammaloop.source_path = "${inputs.gammaloop}";
      symbolica.source_path = "${inputs.symbolica}";
      feyngraph.source_path = "${inputs.feyngraph}";
    }
  );
  manifestScript = pkgs.writeText "generation-manifest.py" ''
    import json
    from pathlib import Path
    import sys
    sys.path.insert(0, ${builtins.toJSON "${recipeRoot}/suites/generation"})
    from prepare import cargo_manifest
    Path(sys.argv[2]).write_text(cargo_manifest(sys.argv[1], json.loads(Path(sys.argv[3]).read_text())))
  '';
  mkGeneration =
    kind: engines:
    let
      native = lib.elem kind [
        "count"
        "materialized"
      ];
      lockContents =
        builtins.replaceStrings
          [ "gammabench-generation-${if native then "count" else "signed-routed"}" ]
          [ "gammabench-generation-${kind}" ]
          (builtins.readFile (if native then ./locks/native.lock else ./locks/rich.lock));
      lock = pkgs.writeText "generation-${kind}-Cargo.lock" lockContents;
      driverSources = recipeRoot + "/suites/generation/drivers/${kind}";
      driverSource =
        dummy:
        pkgs.runCommand "generation-${kind}-${if dummy then "dependencies" else "source"}"
          {
            nativeBuildInputs = [ pkgs.python313 ];
          }
          ''
            mkdir -p "$out/src"
            ${
              if dummy then ''echo 'fn main() {}' > "$out/src/main.rs"'' else ''cp -R ${driverSources}/. "$out/"''
            }
            python ${manifestScript} ${kind} "$out/Cargo.toml" ${helper}
            cp ${lock} "$out/Cargo.lock"
          '';
      cargoVendorDir = craneLib.vendorCargoDeps {
        cargoLockContents = lockContents;
        overrideVendorGitCheckout =
          packages: drv:
          if lib.any (package: package.name == "symbolica-typst-atom-payload") packages then
            drv.overrideAttrs (_: {
              src = builtins.fetchTree {
                type = "github";
                owner = "symbolica-dev";
                repo = "symbolica-typst-plugin";
                rev = lib.last (lib.splitString "#" (builtins.head packages).source);
              };
            })
          else
            drv;
      };
      commonArgs = {
        pname = "gammabench-generation-${kind}";
        version = "0.1.0";
        inherit cargoVendorDir;
        strictDeps = true;
        cargoExtraArgs = "--locked --offline";
        nativeBuildInputs = [
          pkgs.pkg-config
          pkgs.gcc
          pkgs.gnum4
          pkgs.python313
        ];
        buildInputs = [
          pkgs.gmp
          pkgs.mpfr
          pkgs.libmpc
          pkgs.openssl
        ];
        CC = "${pkgs.gcc}/bin/gcc";
        CXX = "${pkgs.gcc}/bin/g++";
        RUSTFLAGS = "-C linker=${pkgs.gcc}/bin/gcc";
        NO_SYMBOLICA_OEM_LICENSE = "1";
        doCheck = false;
      };
      driver = craneLib.buildPackage (
        commonArgs
        // {
          src = driverSource false;
          # Generated trees share epoch mtimes with the dependency dummy.
          # Remove only its root artifact after inheritance; keep compiled deps.
          preBuild = "cargo clean --release --locked --offline -p gammabench-generation-${kind}";
          cargoArtifacts = craneLib.buildDepsOnly (commonArgs // { dummySrc = driverSource true; });
        }
      );
      externalBins =
        lib.optionalAttrs (lib.elem "qgraf" engines) { qgraf = "${external.qgraf}/bin/qgraf"; }
        // lib.optionalAttrs (lib.elem "nauty" engines) (
          lib.genAttrs [ "geng" "multig" "geng-materialized" "gentreeg-materialized" ] (
            name: "${external.nauty}/bin/${name}"
          )
        );
      selectedSources =
        (lib.filterAttrs (name: _: name != "feyngraph" || lib.elem "feyngraph" engines) sources)
        // lib.optionalAttrs (lib.elem "qgraf" engines) { qgraf = archiveSource "qgraf"; }
        // lib.optionalAttrs (lib.elem "nauty" engines) { nauty = archiveSource "nauty"; };
      config = pkgs.writeText "generation-${kind}-suite-config.json" (
        builtins.toJSON {
          contract = kind;
          binary = "${driver}/bin/gammabench-generation-${kind}";
          inherit engines;
          external = externalBins;
        }
      );
      base = mkBenchmark {
        name = "generation-${kind}";
        definition = {
          schema_version = 1;
          id = "generation-${kind}";
          tools = { };
        };
        assemble = ''
          # Argument rejection runs before every adapter's measurement clock.
          # In particular, a dependency dummy must never become a benchmark.
          if ${driver}/bin/gammabench-generation-${kind} __invalid_engine__ __invalid_case__ >/dev/null 2>&1; then
            echo "Generation adapter accepted invalid arguments (dependency dummy reused)" >&2
            exit 1
          fi
          python ${./generation-suite.py} ${recipeRoot} "$out" ${config}
        '';
        sources = selectedSources;
        subjects = lib.filterAttrs (engine: _: lib.elem engine engines) {
          linnet = "gammaloop";
          graphica = "symbolica";
          feyngraph = "feyngraph";
        };
        build = {
          inherit profile;
          external = externalBins;
          adapter = {
            path = toString driver;
            derivation = builtins.unsafeDiscardStringContext driver.drvPath;
            cargo_lock_sha256 = builtins.hashString "sha256" lockContents;
          };
        };
        comparisonContext = {
          dependencies = lib.mapAttrs (_: value: builtins.removeAttrs value [ "store_path" ]) selectedSources;
          inherit profile;
          external_profile =
            lib.optionalAttrs (lib.elem "qgraf" engines) { qgraf = "gfortran -O3"; }
            // lib.optionalAttrs (lib.elem "nauty" engines) {
              nauty = "generic, no-popcnt; public OUTPROC -O3 -g -DWORDSIZE=32 -DMAXN=32";
            };
          inherit (infrastructure)
            toolchain
            nixpkgs
            hyperfine
            framework_sha256
            ;
          assembly_sha256 = builtins.hashFile "sha256" ./generation-suite.py;
          prepare_sha256 = builtins.hashFile "sha256" (recipeRoot + "/suites/generation/prepare.py");
          validator_sha256 = builtins.hashFile "sha256" (recipeRoot + "/suites/generation/validate.py");
          cargo_lock_sha256 = builtins.hashString "sha256" lockContents;
          adapter_sources = lib.mapAttrs (_: value: builtins.hashFile "sha256" value) (
            lib.listToAttrs (
              map
                (name: {
                  inherit name;
                  value = driverSources + "/src/${name}";
                })
                (
                  lib.filter (name: lib.hasSuffix ".rs" name) (
                    builtins.attrNames (builtins.readDir (driverSources + "/src"))
                  )
                )
            )
          );
        };
      };
    in
    {
      package = base.package;
      inherit (base) app runner;
    };
  benchmarks = {
    generation-count = mkGeneration "count" [
      "linnet"
      "graphica"
      "feyngraph"
    ];
    generation-materialized = mkGeneration "materialized" [
      "linnet"
      "graphica"
      "feyngraph"
      "qgraf"
      "nauty"
    ];
    generation-signed-routed = mkGeneration "signed-routed" [
      "linnet"
      "qgraf"
    ];
    generation-full-public = mkGeneration "full-public" [ "linnet" ];
  };
in
{
  packages =
    (lib.mapAttrs (_: value: value.package) benchmarks)
    // lib.mapAttrs' (name: value: lib.nameValuePair "bench-${name}" value.runner) benchmarks;
  apps = lib.mapAttrs (_: value: value.app) benchmarks;
}
