{
  inputs,
  system,
  sourceRoot,
  pkgs,
  craneLib,
  toolchain,
}:
let
  inherit (pkgs) lib;
  infrastructure = {
    inherit system;
    nixpkgs = {
      revision = inputs.nixpkgs.rev;
      nar_hash = inputs.nixpkgs.narHash;
    };
    toolchain = {
      path = toString toolchain;
      derivation = builtins.unsafeDiscardStringContext toolchain.drvPath;
    };
    hyperfine = {
      version = "2.0.0";
      revision = inputs.hyperfine-src.rev;
      path = toString hyperfine;
      derivation = builtins.unsafeDiscardStringContext hyperfine.drvPath;
    };
    framework_sha256 = builtins.hashString "sha256" (
      builtins.toJSON (
        map
          (name: {
            inherit name;
            sha256 = builtins.hashFile "sha256" (sourceRoot + "/src/gammabench/${name}");
          })
          (
            lib.filter (name: lib.hasSuffix ".py" name) (
              builtins.attrNames (builtins.readDir (sourceRoot + "/src/gammabench"))
            )
          )
      )
    );
  };
  frameworkSource = lib.fileset.toSource {
    root = sourceRoot;
    fileset = lib.fileset.unions [
      (sourceRoot + "/src")
      (sourceRoot + "/pyproject.toml")
    ];
  };
  gammabench = pkgs.python313Packages.buildPythonApplication {
    pname = "gammabench";
    version = "0.1.0";
    src = frameworkSource;
    pyproject = true;
    build-system = [ pkgs.python313Packages.setuptools ];
    # The interpreter and its embedded package paths belong to this closure.
    # CLI --env overrides are applied later, when spawning measured commands.
    makeWrapperArgs = [
      "--prefix"
      "PATH"
      ":"
      (lib.makeBinPath [
        hyperfine
        pkgs.typst
      ])
    ]
    ++
      lib.concatMap
        (name: [
          "--unset"
          name
        ])
        [
          "LD_LIBRARY_PATH"
          "DYLD_LIBRARY_PATH"
          "PYTHONHOME"
          "PYTHONPATH"
        ];
    doCheck = false;
  };
  hyperfineArgs = {
    pname = "hyperfine";
    version = "2.0.0";
    src = inputs.hyperfine-src;
    cargoLock = inputs.hyperfine-src + "/Cargo.lock";
    cargoVendorDir = craneLib.vendorCargoDeps { cargoLock = inputs.hyperfine-src + "/Cargo.lock"; };
    doCheck = false;
  };
  hyperfine = craneLib.buildPackage (
    hyperfineArgs
    // {
      cargoArtifacts = craneLib.buildDepsOnly hyperfineArgs;
      meta = {
        description = "Command-line benchmarking tool";
        license = with lib.licenses; [
          mit
          asl20
        ];
      };
    }
  );
  mkBenchmark = import ./mk-benchmark.nix {
    inherit
      pkgs
      gammabench
      hyperfine
      infrastructure
      ;
  };
  exampleDefinition = builtins.fromJSON (
    builtins.readFile (sourceRoot + "/suites/examples/sum.json")
  );
  example = mkBenchmark {
    name = "integer-sum";
    definition = exampleDefinition // {
      tools.python = {
        kind = "executable";
        path = "${pkgs.python313}/bin/python3";
        version = "Python ${pkgs.python313.version}";
        version_command = [
          "{tool}"
          "--version"
        ];
      };
    };
    files =
      map
        (name: {
          source = sourceRoot + "/suites/examples/${name}";
          target = name;
        })
        [
          "sum.py"
          "workload.json"
        ];
    comparisonContext = {
      benchmark_definition_sha256 = builtins.hashFile "sha256" (sourceRoot + "/suites/examples/sum.json");
      adapter_sha256 = builtins.hashFile "sha256" (sourceRoot + "/suites/examples/sum.py");
      python = toString pkgs.python313;
      inherit (infrastructure) nixpkgs framework_sha256 hyperfine;
    };
  };
  sortSource = sourceRoot + "/suites/examples/rust-sort";
  sortDriver =
    pkgs.runCommand "gammabench-rust-sort"
      {
        nativeBuildInputs = [
          toolchain
          pkgs.stdenv.cc
        ];
      }
      ''
        mkdir -p "$out/bin"
        rustc --edition=2024 -Copt-level=3 -Clinker=${pkgs.stdenv.cc}/bin/cc ${sortSource + "/main.rs"} -o "$out/bin/gammabench-rust-sort"
      '';
  sortDefinition = builtins.fromJSON (builtins.readFile (sortSource + "/suite.json"));
  rustSort = mkBenchmark {
    name = "rust-sort";
    definition = sortDefinition // {
      tools.sort-driver = sortDefinition.tools.sort-driver // {
        path = "${sortDriver}/bin/gammabench-rust-sort";
      };
    };
    files = [
      {
        source = sortSource + "/workload.txt";
        target = "workload.txt";
      }
    ];
    build.adapter = {
      path = toString sortDriver;
      derivation = builtins.unsafeDiscardStringContext sortDriver.drvPath;
    };
    comparisonContext = {
      benchmark_definition_sha256 = builtins.hashFile "sha256" (sortSource + "/suite.json");
      adapter_sha256 = builtins.hashFile "sha256" (sortSource + "/main.rs");
      profile = {
        opt_level = 3;
        edition = "2024";
      };
      inherit (infrastructure)
        nixpkgs
        toolchain
        framework_sha256
        hyperfine
        ;
    };
  };
  external = import ./external-tools.nix { inherit pkgs sourceRoot; };
  generation = import ./generation.nix {
    inherit
      inputs
      system
      sourceRoot
      pkgs
      craneLib
      toolchain
      mkBenchmark
      infrastructure
      external
      ;
  };
  builtinPackages = {
    default = example.package;
    inherit gammabench hyperfine;
    inherit (pkgs) nodejs;
    integer-sum = example.package;
    bench-integer-sum = example.runner;
    rust-sort = rustSort.package;
    bench-rust-sort = rustSort.runner;
    inherit (external) qgraf nauty;
  }
  // generation.packages;
  builtinApps = {
    default = example.app;
    integer-sum = example.app;
    rust-sort = rustSort.app;
  }
  // generation.apps;
  registered = import ./registered.nix {
    inherit
      pkgs
      inputs
      craneLib
      sourceRoot
      toolchain
      mkBenchmark
      infrastructure
      gammabench
      ;
    reservedNames = builtins.attrNames builtinPackages ++ builtins.attrNames builtinApps;
  };
in
{
  inherit mkBenchmark;
  packages = builtinPackages // registered.packages;
  apps = builtinApps // registered.apps;
  checks = registered.checks // {
    package-definition =
      pkgs.runCommand "gammabench-package-definition" { nativeBuildInputs = [ gammabench ]; }
        ''
          gammabench validate ${example.package}/suite.json
          touch "$out"
        '';
    runtime-environment = pkgs.runCommand "gammabench-runtime-environment" { } ''
      mkdir -p "$TMPDIR/injected/gammabench"
      echo 'raise RuntimeError("Ambient PYTHONPATH replaced the packaged framework")' > "$TMPDIR/injected/gammabench/__init__.py"
      export LD_LIBRARY_PATH="$TMPDIR/injected"
      export DYLD_LIBRARY_PATH="$TMPDIR/injected"
      export PYTHONHOME="$TMPDIR/no-python-home"
      export PYTHONPATH="$TMPDIR/injected"
      ${gammabench}/bin/gammabench --help > framework-help.txt
      ${gammabench}/bin/gammabench validate ${example.package}/suite.json
      ${example.runner}/bin/bench-integer-sum --help > app-help.txt
      touch "$out"
    '';
  };
  devShells.default = pkgs.mkShell {
    packages = [
      gammabench
      hyperfine
      toolchain
      pkgs.typst
      pkgs.nodejs
    ];
  };
}
