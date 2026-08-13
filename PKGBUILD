# Maintainer: ShedOS <https://github.com/Theshedman/shedos>
#
# The shedman dispatcher and the verbs it was built around: installing and
# removing packages, the interactive upgrade, the declared-state reconciler,
# health, status, logs, snapshots and rollback. Verbs from anywhere else ship
# from their own package with a declaration beside them.

pkgname=shedman
pkgver=2026.08.13
pkgrel=1
pkgdesc='The ShedOS package manager and system CLI'
arch=('any')
url='https://github.com/shed-os/shedman'
license=('GPL-3.0-or-later')

makedepends=(
    'scdoc'            # renders man/*.scd → /usr/share/man/man1/*.1
)

depends=(
    'bash'
    'coreutils'        # sha256sum, install
    'diffutils'        # the 3-way merges and the drift diffs
    'sudo'
    'systemd'          # every verb that reads or drives a unit
    'pacman-contrib'   # checkupdates
    'yay'              # the AUR half of install/uninstall/update/updates
    'kitty'            # update runs interactively; config --review opens a TUI
    'libnotify'        # notify-send from health, conflicts, doctor, updates
    'python'
    'python-textual'   # the datetime, status, logs and merge TUIs
    'python-rich'      # transitive dep of textual, declared for clarity
    'python-pygments'  # syntax highlighting in the merge TUI hunk panes
    'python-tomlkit'   # format-preserving system.toml writes
    'shedos-theme-engine'  # shedos_palette, which the datetime, logs, status,
                       # upgrade-history and _config-review TUIs import to
                       # colour themselves
    'snapper'          # the pre/post snapshots rollback restores from
    'btrfs-progs'      # rollback and health call `btrfs subvolume`
    'lm_sensors'       # the health CPU-temp metric
    'smartmontools'    # the health SMART verdict
    'ufw'              # the `[network.firewall]` reconciler shells out to it
)

optdepends=(
    'postgresql: shedman db bootstraps and reports on a local cluster'
    'reflector: shedman update refreshes the mirrorlist before it upgrades'
    'code: opt-in GUI merge backend for shedman config --review --gui (satisfied by code from extra or visual-studio-code-bin from AUR)'
    'bash-completion: tab-complete subcommands and flags in bash'
    'zsh: tab-complete subcommands and flags in zsh (via /usr/share/zsh/site-functions/_shedman)'
    'fish: tab-complete subcommands and flags in fish (via /usr/share/fish/vendor_completions.d/shedman.fish)'
)

backup=(
    'etc/shedman/shedman.toml'
)

source=("git+https://github.com/shed-os/shedman.git#tag=$pkgver")
sha256sums=('SKIP')

prepare() {
    cd "$srcdir/shedman"

    # Render the scdoc sources to groff here rather than in package(), so a
    # malformed page surfaces before anything is installed.
    install -d man/build
    for src in man/*.scd; do
        scdoc < "$src" > "man/build/$(basename "${src%.scd}")"
    done
}

package() {
    cd "$srcdir/shedman"

    install -Dm755 tree/usr/bin/shedman "$pkgdir/usr/bin/shedman"
    install -Dm644 tree/etc/shedman/shedman.toml "$pkgdir/etc/shedman/shedman.toml"

    # The declarations are the manifest: a verb ships because one names it, so
    # a verb added without its declaration does not ship silently — it does not
    # ship at all, and the pipeline's completeness check says which way round
    # the mistake was.
    local _decl _name
    for _decl in tree/usr/share/shedman/verbs.d/*.toml; do
        _name=$(sed -n 's/^name = "\(.*\)"$/\1/p' "$_decl")
        install -Dm755 "tree/usr/libexec/shedman/$_name" \
            "$pkgdir/usr/libexec/shedman/$_name"
        install -Dm644 "$_decl" \
            "$pkgdir/usr/share/shedman/verbs.d/$(basename "$_decl")"
    done

    # Shared plan engine; both `shedman apply` and `shedman doctor` add
    # /usr/lib/shedos to sys.path and `import apply_core`.
    install -Dm644 tree/usr/lib/shedos/apply_core.py \
        "$pkgdir/usr/lib/shedos/apply_core.py"

    install -Dm644 tree/usr/share/bash-completion/completions/shedman \
        "$pkgdir/usr/share/bash-completion/completions/shedman"
    install -Dm644 tree/usr/share/zsh/site-functions/_shedman \
        "$pkgdir/usr/share/zsh/site-functions/_shedman"
    install -Dm644 tree/usr/share/fish/vendor_completions.d/shedman.fish \
        "$pkgdir/usr/share/fish/vendor_completions.d/shedman.fish"

    local _page
    for _page in man/build/*.1; do
        install -Dm644 "$_page" \
            "$pkgdir/usr/share/man/man1/$(basename "$_page")"
    done
}
