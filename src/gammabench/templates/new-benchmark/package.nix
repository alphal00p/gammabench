{
  pkgs,
  sourceRoot,
  toolchain,
  mkBenchmark,
  infrastructure,
  ...
}:
let
  source = sourceRoot + "/suites/@@BENCHMARK_ID@@";
  definition = builtins.fromJSON (builtins.readFile (source + "/suite.json"));
  driver = pkgs.runCommand "gammabench-@@BENCHMARK_ID@@-driver"
    { nativeBuildInputs = [ toolchain pkgs.stdenv.cc ]; }
    ''
      mkdir -p "$out/bin"
      rustc --edition=2024 -Copt-level=3 -Clinker=${pkgs.stdenv.cc}/bin/cc \
        ${source + "/main.rs"} -o "$out/bin/@@BENCHMARK_ID@@"
    '';
in
mkBenchmark {
  name = "@@BENCHMARK_ID@@";
  definition = definition // {
    tools = definition.tools // {
      driver = definition.tools.driver // { path = "${driver}/bin/@@BENCHMARK_ID@@"; };
    };
  };
  files = [ { source = source + "/workload.txt"; target = "workload.txt"; } ];
  # When measuring an upstream library, declare its locked source in sources and
  # map the task engine to it in subjects. A starter has no invented Git revision.
  build.adapter = {
    path = toString driver;
    derivation = builtins.unsafeDiscardStringContext driver.drvPath;
  };
  comparisonContext = {
    benchmark_definition_sha256 = builtins.hashFile "sha256" (source + "/suite.json");
    adapter_sha256 = builtins.hashFile "sha256" (source + "/main.rs");
    profile = { opt_level = 3; edition = "2024"; };
    inherit (infrastructure) nixpkgs toolchain framework_sha256 hyperfine;
  };
}
