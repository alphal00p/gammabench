# Native controls use the same pinned sources and public callbacks as prepare.py.
{
  pkgs,
  sourceRoot ? ../.,
}:
let
  inherit (pkgs) lib;
  pins = (builtins.fromJSON (builtins.readFile (sourceRoot + "/tools.lock.json"))).tools;
  callback = sourceRoot + "/suites/generation/native/nauty_callback.c";
  archive =
    name:
    (pkgs.fetchurl {
      inherit (pins.${name}) url sha256;
    }).overrideAttrs
      (
        _:
        lib.optionalAttrs (name == "qgraf") {
          # QGRAF's source and binary must remain local, including the fetch output.
          preferLocalBuild = true;
          allowSubstitutes = false;
        }
      );
  sourceReceipt =
    name:
    builtins.toJSON {
      schema_version = 1;
      tool = name;
      source = pins.${name};
    };
in
{
  qgraf = pkgs.stdenv.mkDerivation {
    pname = "gammabench-qgraf";
    inherit (pins.qgraf) version;
    src = archive "qgraf";
    nativeBuildInputs = [ pkgs.gfortran ];
    # The upstream archive contains files directly, without a containing folder.
    sourceRoot = ".";
    dontConfigure = true;
    buildPhase = ''
      runHook preBuild
      gfortran -O3 qgraf-${pins.qgraf.version}.f08 -o qgraf
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      install -Dm755 qgraf "$out/bin/qgraf"
      install -Dm644 ${pkgs.writeText "qgraf-source.json" (sourceReceipt "qgraf")} \
        "$out/share/gammabench/source.json"
      runHook postInstall
    '';
    preferLocalBuild = true;
    allowSubstitutes = false;
    meta = {
      description = "Locally built QGRAF control for gammabench";
      homepage = "https://qgraf.tecnico.ulisboa.pt/";
      license = lib.licenses.unfree;
      platforms = lib.platforms.unix;
      hydraPlatforms = [ ];
    };
  };

  nauty = pkgs.stdenv.mkDerivation {
    pname = "gammabench-nauty";
    inherit (pins.nauty) version;
    src = archive "nauty";
    # Keep generated binaries independent of the build machine's instruction set.
    dontDisableStatic = true;
    configureFlags = [
      "--enable-static"
      "--disable-shared"
      "--enable-generic"
      "--disable-popcnt"
    ];
    enableParallelBuilding = true;
    buildPhase = ''
      runHook preBuild
      make -j"$NIX_BUILD_CORES" geng gentreeg multig nautyW.a
      for program in geng gentreeg; do
        if [ "$program" = geng ]; then
          function=materialized_graph
          signature='graph *'
          callback_flags=-DMATERIALIZED_GENG
        else
          function=materialized_tree
          signature='int *'
          callback_flags=
        fi
        printf '#include "nauty.h"\nvoid %s(FILE *, %s, int);\n' \
          "$function" "$signature" > "$program-callback.h"
        $CC -O3 -g -DWORDSIZE=32 -DMAXN=32 -DOUTPROC="$function" \
          $callback_flags -I . -include "$program-callback.h" \
          "$program.c" ${callback} nautyW.a -lm -o "$program-materialized"
      done
      runHook postBuild
    '';
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      ./geng-materialized -q -l -c 3 2:2 > graph.txt
      ./gentreeg-materialized -q 3 > tree.txt
      for output in graph.txt tree.txt; do
        awk 'NF != 9 || $1 != 3 || $2 != 2 || $3 != 2 { exit 1 }
             END { if (NR != 1) exit 1 }' "$output"
      done
      runHook postCheck
    '';
    installPhase = ''
      runHook preInstall
      for program in geng multig geng-materialized gentreeg-materialized; do
        install -Dm755 "$program" "$out/bin/$program"
      done
      install -Dm644 COPYRIGHT "$out/share/licenses/nauty/COPYRIGHT"
      install -Dm644 ${sourceRoot + "/LICENSE"} "$out/share/licenses/gammabench/LICENSE"
      install -Dm644 ${
        pkgs.writeText "nauty-source.json" (
          builtins.toJSON {
            schema_version = 1;
            tool = "nauty";
            source = pins.nauty;
            callback_sha256 = builtins.hashFile "sha256" callback;
          }
        )
      } "$out/share/gammabench/source.json"
      runHook postInstall
    '';
    meta = {
      description = "Pinned nauty controls and materialized public OUTPROC adapters";
      homepage = "https://pallini.di.uniroma1.it/";
      license = [
        lib.licenses.asl20
        lib.licenses.mit
      ];
      platforms = lib.platforms.unix;
    };
  };
}
