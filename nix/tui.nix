# nix/tui.nix — ZedClaw TUI (Ink/React) compiled with tsc and bundled
{ pkgs, zedclawNpmLib, ... }:
let
  src = ../ui-tui;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-NpuD8yaSzQ9jAkrTGjbNFF+0/jfvxCajYhgFe7Zh7j0=";
  };

  npm = zedclawNpmLib.mkNpmPassthru { folder = "ui-tui"; attr = "tui"; pname = "zedclaw-tui"; };

  packageJson = builtins.fromJSON (builtins.readFile (src + "/package.json"));
  version = packageJson.version;
in
pkgs.buildNpmPackage (npm // {
  pname = "zedclaw-tui";
  inherit src npmDeps version;

  doCheck = false;
  npmFlags = [ "--legacy-peer-deps" ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/zedclaw-tui

    # Single self-contained bundle built by scripts/build.mjs (esbuild).
    cp -r dist $out/lib/zedclaw-tui/dist

    # package.json kept for "type": "module" resolution on `node dist/entry.js`.
    cp package.json $out/lib/zedclaw-tui/

    runHook postInstall
  '';
})
