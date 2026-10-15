{
  pkgs,
  gammabench,
  hyperfine,
  infrastructure,
}:
{
  name,
  definition,
  files ? [ ],
  sources ? { },
  subjects ? { },
  build ? { },
  comparisonContext ? { },
  assemble ? "",
}:
assert pkgs.lib.all (
  file:
  !(pkgs.lib.hasPrefix "/" file.target)
  && !(builtins.elem ".." (pkgs.lib.splitString "/" file.target))
) files;
let
  inherit (pkgs) lib;
  receipt = {
    schema_version = 1;
    inherit sources subjects;
    build = infrastructure // build;
    comparison_context = comparisonContext;
  };
  suite = definition // {
    nix_build_receipt = "nix-build-receipt.json";
  };
  package =
    pkgs.runCommand "gammabench-${name}"
      {
        nativeBuildInputs = [ pkgs.python313 ];
        passthru = { inherit receipt; };
      }
      ''
        mkdir -p "$out"
        ${lib.concatMapStringsSep "\n" (file: ''
          mkdir -p "$out/$(dirname ${lib.escapeShellArg file.target})"
          cp -R ${lib.escapeShellArg "${file.source}"} "$out/"${lib.escapeShellArg file.target}
        '') files}
        cp ${pkgs.writeText "${name}-suite.json" (builtins.toJSON suite)} "$out/suite.json"
        cp ${pkgs.writeText "${name}-receipt.json" (builtins.toJSON receipt)} "$out/nix-build-receipt.json"
        chmod u+w "$out/suite.json" "$out/nix-build-receipt.json"
        ${assemble}
        python ${./seal-package.py} "$out"
      '';
  runner = pkgs.writeShellApplication {
    name = "bench-${name}";
    text = ''
      # Keep ambient Python installations out of the packaged runtime.
      unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH PYTHONHOME PYTHONPATH
      if [ "$#" -lt 1 ]; then
        echo 'Usage: bench-${name} NEW-SNAPSHOT-DIRECTORY [gammabench run options]' >&2
        exit 2
      fi
      destination="$1"
      shift
      exec ${gammabench}/bin/gammabench run ${package}/suite.json "$destination" \
        --backend hyperfine --hyperfine ${hyperfine}/bin/hyperfine "$@"
    '';
  };
in
{
  inherit package runner;
  app = {
    type = "app";
    program = "${runner}/bin/bench-${name}";
  };
}
