#!/bin/sh
# Gravitee Lab on macOS and Linux: from a bare machine to the Lab UI in your browser, and the command gravitee-lab for next
# time. In a terminal (no sudo: it asks when a step needs it):
#
#   curl -fsSL https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.sh | sh
#
# It uses what the machine has and adds only what is missing: the tools the lab needs (on macOS, Apple's command line tools;
# on Linux, from the package manager, after asking), the clone in ~/gravitee-lab, the command gravitee-lab in ~/.local/bin and,
# when that folder is not on the PATH, the line of your shell's profile that puts it there. Then it opens the Lab UI, which
# checks Docker and says how to install or start it. It never deletes anything, and can run again at any time.
#
# GRAVITEE_LAB_DIR names the folder of the clone, GRAVITEE_LAB_REPOSITORY and GRAVITEE_LAB_BRANCH what to clone (a fork, a
# branch), GRAVITEE_LAB_YES=1 answers yes to its questions (with no terminal to ask in), and GRAVITEE_LAB_NO_START=1 stops
# before the Lab UI. On Windows, use install.ps1.
#
# Everything is in functions that run on the last line: a download cut halfway runs nothing.

help=https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md
marker='# Added by Gravitee Lab'
steps=4

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    bold=$(printf '\033[1m') cyan=$(printf '\033[36m') green=$(printf '\033[32m') yellow=$(printf '\033[33m') off=$(printf '\033[0m')
else
    bold='' cyan='' green='' yellow='' off=''
fi

step() { printf '\n%s[%s/%s] %s%s\n' "$cyan" "$1" "$steps" "$2" "$off"; }
say() { printf '      %s\n' "$1"; }
ok() { printf '      %s%s%s\n' "$green" "$1" "$off"; }
halt() {
    printf '\n%s%s%s\nHelp: %s%s\n' "$yellow" "$1" "$off" "$help" "${2:-}"
    exit 1
}

# A question, answered from the terminal: the script itself is what `curl | sh` gives as input. Yes with GRAVITEE_LAB_YES=1;
# no when there is no terminal to ask in
ask() {
    [ "${GRAVITEE_LAB_YES:-}" = 1 ] && return 0
    if ! { true < /dev/tty; } 2> /dev/null; then
        return 1
    fi
    printf '      %s [Y/n] ' "$1"
    read -r answer < /dev/tty || return 1
    case $answer in
        '' | [yY]*) return 0 ;;
        *) return 1 ;;
    esac
}

# Python 3.9 or later, as python3 or beside an older one (python3.11): the Makefile looks for the same names
python_ok() {
    for python in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
        command -v "$python" > /dev/null 2>&1 && "$python" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2> /dev/null && return
    done
    return 1
}

# The tools of the lab that are missing, by the name of their command
missing() {
    for tool in git make openssl; do
        command -v "$tool" > /dev/null 2>&1 || printf '%s ' "$tool"
    done
    python_ok || printf 'python3 '
}

# macOS: Apple's command line tools bring git, make and python3, and the system has openssl. Without them, their commands are
# stubs that open the install window: it is opened once, then the tools are waited for
mac_tools() {
    if xcode-select -p > /dev/null 2>&1 && [ -z "$(missing)" ]; then
        ok 'All there'
        return
    fi
    if ! xcode-select -p > /dev/null 2>&1; then
        xcode-select --install > /dev/null 2>&1
        say "macOS opens a window for its command line tools: click Install, then Agree. About 1 GB, a few minutes."
        say 'Waiting for them...'
        waited=0
        until xcode-select -p > /dev/null 2>&1; do
            sleep 5
            waited=$((waited + 5))
            [ $waited -lt 3600 ] || halt "The command line tools did not install. If the window said the software could not be installed, free some disk space, then run this command again." '#macos'
        done
    fi
    gone=$(missing)
    [ -z "$gone" ] || halt "The command line tools are installed and ${gone}is still missing. Run: sudo xcode-select --reset, then run this command again." '#macos'
    ok 'All there'
}

# Linux: what is missing, from the package manager, as root through sudo. Each manager has its own name for Python
linux_tools() {
    gone=$(missing)
    if [ -z "$gone" ]; then
        ok 'All there'
        return
    fi
    packages=$gone
    # A system whose python3 is older than 3.9 ships a later one under another name: the first its manager has is installed
    newer=''
    if command -v apt-get > /dev/null 2>&1; then
        install='apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq'
        newer='python3.12 python3.11 python3.10 python3.9'
    elif command -v dnf > /dev/null 2>&1; then
        install='dnf install -y -q'
        newer='python3.12 python3.11 python39'
    elif command -v yum > /dev/null 2>&1; then
        install='yum install -y -q'
        newer='python3.11 python39'
    elif command -v zypper > /dev/null 2>&1; then
        install='zypper --non-interactive --quiet install'
        newer='python313 python312 python311 python310 python39'
    elif command -v pacman > /dev/null 2>&1; then
        install='pacman -S --needed --noconfirm'
        packages=$(echo "$gone" | sed 's/python3/python/')
    elif command -v apk > /dev/null 2>&1; then
        install='apk add --quiet'
    else
        halt "Install ${gone}with the package manager of this system, then run this command again." '#linux'
    fi
    if [ "$(id -u)" = 0 ]; then
        root=''
    elif command -v sudo > /dev/null 2>&1; then
        root='sudo'
    else
        halt "${gone}is missing and sudo is not here to install it. Install ${gone}as root, then run this command again." '#linux'
    fi
    ask "Install ${packages}with ${install%% *}?${root:+ sudo asks for your password.}" \
        || halt "Install ${packages}with ${install%% *}, then run this command again." '#linux'
    say "Installing ${packages}"
    log=$(mktemp)
    # shellcheck disable=SC2086
    if ! $root sh -c "$install $packages" > "$log" 2>&1; then
        tail -n 5 "$log" | sed 's/^/      /'
        rm -f "$log"
        halt "${packages}did not install: ${install%% *} says why above. Is the internet reachable from this machine?" '#linux'
    fi
    if ! python_ok; then
        say 'The python3 of this system is older than 3.9: installing a later one beside it'
        for package in $newer; do
            # shellcheck disable=SC2086
            $root sh -c "$install $package" > "$log" 2>&1 && python_ok && packages="$packages$package " && break
        done
    fi
    rm -f "$log"
    python_ok || halt "Python 3.9 or later is missing, and the package manager of this system has none: install one, then run this command again." '#linux'
    gone=$(missing)
    [ -z "$gone" ] || halt "${gone}is still missing: install it, then run this command again." '#linux'
    ok "Installed ${packages% }"
}

# Docker's command: on the PATH, or where Docker Desktop puts it when a terminal opened before it was installed does not
# find it yet (the Lab UI looks in the same places)
docker_cli() {
    for docker in "$(command -v docker 2> /dev/null)" /usr/local/bin/docker "$HOME/.docker/bin/docker" \
        /Applications/Docker.app/Contents/Resources/bin/docker; do
        [ -n "$docker" ] && [ -x "$docker" ] && echo "$docker" && return
    done
}

# A folder as a person reads it: ~ for the home folder
# shellcheck disable=SC2088 # the ~ is what is shown, not a path
shown() {
    case $1 in
        "$HOME"/*) printf '~/%s\n' "${1#"$HOME"/}" ;;
        *) echo "$1" ;;
    esac
}

# The clone: one that is there is used as it is, whatever its origin (a fork) or its branch, and the Lab UI updates it
clone() {
    if [ -e "$lab" ]; then
        if [ -f "$lab/lab-ui/server.py" ] && [ -f "$lab/bin/gravitee-lab" ] && git -C "$lab" rev-parse --git-dir > /dev/null 2>&1; then
            ok 'Already there'
            return
        fi
        if [ -f "$lab/lab-ui/server.py" ]; then
            halt "$(shown "$lab") holds a Gravitee Lab older than the command gravitee-lab. Update it (cd $(shown "$lab") && make update), then run this command again."
        fi
        halt "$(shown "$lab") holds something else. Move it, or name another folder with GRAVITEE_LAB_DIR, then run this command again."
    fi
    git clone --quiet --branch "$branch" "$repository" "$lab" \
        || halt "The clone failed: git says why above. Behind a proxy that inspects HTTPS, git needs the certificate of your company (Corporate proxy, in docs/troubleshooting.md)." '#prerequisites'
    ok 'Cloned'
}

# The profile that the shell of this person reads when a terminal opens
profile() {
    case ${SHELL##*/} in
        zsh) echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash) [ "$system" = macOS ] && echo "$HOME/.bash_profile" || echo "$HOME/.bashrc" ;;
        fish) echo "$HOME/.config/fish/conf.d/gravitee-lab.fish" ;;
        *) echo "$HOME/.profile" ;;
    esac
}

# The command: a link in ~/.local/bin to bin/gravitee-lab of the clone, and that folder on the PATH of the next terminals
command_on_path() {
    mkdir -p "$HOME/.local/bin" || halt "$HOME/.local/bin cannot be made."
    if [ -e "$HOME/.local/bin/gravitee-lab" ] && [ ! -L "$HOME/.local/bin/gravitee-lab" ]; then
        halt "$HOME/.local/bin/gravitee-lab is a file of something else. Move it, then run this command again."
    fi
    ln -sf "$lab/bin/gravitee-lab" "$HOME/.local/bin/gravitee-lab"
    case :$PATH: in
        *:"$HOME/.local/bin":*)
            ok 'gravitee-lab is on the PATH'
            return
            ;;
    esac
    file=$(profile)
    if ! grep -qs "$marker" "$file"; then
        mkdir -p "$(dirname "$file")"
        # shellcheck disable=SC2016 # the profile expands $HOME and $PATH, each time a terminal opens
        case $file in
            *.fish) printf 'fish_add_path -g "$HOME/.local/bin"  %s\n' "$marker" >> "$file" ;;
            *) printf '\nexport PATH="$HOME/.local/bin:$PATH"  %s\n' "$marker" >> "$file" ;;
        esac
    fi
    ok "gravitee-lab, in the terminals you open from now on (~/.local/bin is now on the PATH in ${file#"$HOME"/})"
}

main() {
    case $(uname -s) in
        Darwin) system=macOS ;;
        Linux) system=Linux ;;
        MINGW* | MSYS* | CYGWIN*) halt 'On Windows, run this in PowerShell instead: irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex' '#windows-with-wsl-2' ;;
        *) halt "Gravitee Lab runs on macOS, Linux and Windows through WSL 2, not on $(uname -s)." '#prerequisites' ;;
    esac
    if [ -n "${SUDO_USER:-}" ]; then
        halt 'Run this without sudo: it asks for it when a step needs it, and the lab belongs to your user.'
    fi
    lab=${GRAVITEE_LAB_DIR:-$HOME/gravitee-lab}
    case $lab in
        /*) ;;
        *) lab=$PWD/$lab ;;
    esac
    branch=${GRAVITEE_LAB_BRANCH:-main}
    repository=${GRAVITEE_LAB_REPOSITORY:-https://github.com/gravitee-io-labs/gravitee-lab.git}

    printf '%sGravitee Lab for %s%s\n' "$bold" "$system" "$off"
    echo 'It uses what this machine has, adds what is missing, and asks before it installs anything.'

    step 1 'Git, Make, Python 3.9 or later and OpenSSL'
    if [ "$system" = macOS ]; then mac_tools; else linux_tools; fi

    step 2 "Gravitee Lab in $(shown "$lab")"
    clone

    step 3 'The command gravitee-lab'
    command_on_path

    step 4 'Docker'
    docker=$(docker_cli)
    if [ -n "$docker" ] && "$docker" info > /dev/null 2>&1; then
        ok 'Docker answers'
    elif [ -n "$docker" ]; then
        say 'Docker is installed and does not answer: the Lab UI starts it, or says how'
    else
        say 'Docker is not installed: the Lab UI says how to install it, and goes on once it is there'
    fi

    if [ "${GRAVITEE_LAB_NO_START:-}" = 1 ]; then
        printf '\n%sReady. Run gravitee-lab to open the Lab UI.%s\n' "$green" "$off"
        return
    fi
    printf '\n%sOpening the Lab UI in your browser. Keep this terminal open while you use it: Ctrl+C stops the Lab UI, and the containers keep running. Next time, run gravitee-lab.%s\n' "$green" "$off"
    exec "$lab/bin/gravitee-lab" < /dev/null
}

main "$@"
