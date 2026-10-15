{
  description = "Versioned benchmark packages and Hyperfine measurement apps";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/02f5696b0e6097e589076d886b317b83ff0437d7";
    crane.url = "github:ipetkov/crane/eb35abda9f232cc6610b1d1e3200d15c49b7ac54";
    fenix = {
      url = "github:nix-community/fenix/1fc6f36a23fa16e3740987957266f6e4b915c3cd";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.rust-analyzer-src.follows = "";
    };
    hyperfine-src = {
      url = "github:sharkdp/hyperfine/b33755fe1fca8bcc1f182c773ca3ab040bfe517d";
      flake = false;
    };
    gammaloop = {
      url = "github:alphal00p/gammaloop/d83bfc69055908a64808ffbbe7ea345b5f5a8ec5";
      flake = false;
    };
    symbolica = {
      url = "github:symbolica-dev/symbolica/ed2374f1d880d52c3a7ca48cd7c22f4baad5c020";
      flake = false;
    };
    feyngraph = {
      url = "github:Jens-Braun/FeynGraph/15afe2f2c701331c76a0f5d55a752dca099104be";
      flake = false;
    };
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
      crane,
      fenix,
      ...
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      perSystem = forAllSystems (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfreePredicate = package: nixpkgs.lib.getName package == "gammabench-qgraf";
          };
        in
        import ./nix {
          inherit inputs system;
          sourceRoot = ./.;
          inherit pkgs;
          craneLib = (crane.mkLib pkgs).overrideToolchain (
            fenix.packages.${system}.stable.withComponents [
              "cargo"
              "rustc"
              "rust-std"
            ]
          );
          toolchain = fenix.packages.${system}.stable.withComponents [
            "cargo"
            "rustc"
            "rust-std"
          ];
        }
      );
    in
    {
      packages = forAllSystems (system: perSystem.${system}.packages);
      apps = forAllSystems (system: perSystem.${system}.apps);
      checks = forAllSystems (system: perSystem.${system}.checks);
      devShells = forAllSystems (system: perSystem.${system}.devShells);
      lib.mkBenchmark =
        { system, ... }@args: perSystem.${system}.mkBenchmark (builtins.removeAttrs args [ "system" ]);
    };
}
