class Familiar < Formula
  desc "Keyboard-driven kitty overlays for a Claude Code workflow"
  homepage "https://github.com/DenoBY/familiar"
  url "https://github.com/DenoBY/familiar/archive/refs/tags/v0.18.0.tar.gz"
  sha256 "96fd3897b03c361e2916ee9850cd09d4c6efbb5a11f4ad98dbd70b82d1e87c7d"
  license "MIT"
  head "https://github.com/DenoBY/familiar.git", branch: "master"

  depends_on :macos
  depends_on "python@3.13"

  def install
    # Раскладку репозитория сохраняем целиком в libexec.
    libexec.install "bin", "plugins", "config", "VERSION"
    # Обёртка задаёт FAMILIAR_ROOT стабильным opt-путём: китены прописываются в
    # kitty.conf через …/opt/familiar/…, а не версионный Cellar, поэтому переживают
    # `brew upgrade`. Python зовём явно — шебанг скрипта не важен.
    (bin/"familiar").write <<~SH
      #!/bin/bash
      export FAMILIAR_ROOT="#{opt_libexec}"
      exec "#{formula_opt_bin("python@3.13")}/python3.13" "#{opt_libexec}/bin/familiar" "$@"
    SH
  end

  def caveats
    <<~EOS
      familiar is installed but not wired into kitty yet.

      Enable everything (kittens + terminal look):
        familiar enable --all
      Just the kittens, leaving your terminal config alone:
        familiar enable --kittens
      Or pick specific overlays:
        familiar enable session review

      Reload kitty afterwards (Cmd+Ctrl+, on macOS) or restart it.
      Undo any time:  familiar disable   (--restore for a full revert)
    EOS
  end

  test do
    ENV["KITTY_CONFIG_DIRECTORY"] = testpath.to_s

    assert_match "config dir:", shell_output("#{bin}/familiar status")

    shell_output("#{bin}/familiar enable session")
    generated = (testpath/"familiar.conf").read
    assert_match "cc_plugin=session", generated
    assert_match "plugins/session.py", generated
    assert_match "include familiar.conf", (testpath/"kitty.conf").read

    shell_output("#{bin}/familiar disable")
    refute_path_exists testpath/"familiar.conf"
    refute_match ">>> familiar >>>", (testpath/"kitty.conf").read
  end
end
