# nix/packages.nix — ZedClaw package built with uv2nix
{ inputs, ... }:
{
  perSystem =
    { pkgs, inputs', ... }:
    let
      zedclawAgent = pkgs.callPackage ./zedclaw.nix {
        inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
        npm-lockfile-fix = inputs'.npm-lockfile-fix.packages.default;
        # Only embed clean revs — dirtyRev doesn't represent any upstream
        # commit, so comparing it would always claim "update available".
        rev = inputs.self.rev or null;
      };
    in
    {
      packages = {
        default = zedclawAgent;
        tui = zedclawAgent.zedclawTui;
        web = zedclawAgent.zedclawWeb;

        fix-lockfiles = zedclawAgent.zedclawNpmLib.mkFixLockfiles {
          packages = [ zedclawAgent.zedclawTui zedclawAgent.zedclawWeb ];
        };
      };
    };
}
