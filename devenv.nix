{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

{
  # Python 3.13 venv with the test dependencies from requirements-dev.txt.
  languages.python = {
    enable = true;
    package = pkgs.python313;
    venv = {
      enable = true;
      requirements = ./requirements-dev.txt;
    };
  };

  # External binaries invoked by ospd/metadata.py and ospd/executor.py.
  packages = [
    pkgs.imagemagick # magick identify  -> EXIF / dimensions
    pkgs.ffmpeg # ffprobe         -> video creation time
    pkgs.procps # pgrep           -> cloud sync client check
    pkgs.git
  ];

  enterShell = ''
    python3 --version
  '';

  # `devenv test` runs this task (devenv >= 2.0 task graph).
  tasks."devenv:enterTest".exec = "python3 -m pytest -q";
}
