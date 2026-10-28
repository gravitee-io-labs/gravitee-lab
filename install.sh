#!/bin/sh
# Gravitee Lab on macOS and Linux: from a bare machine to the Lab UI in your browser, and the command gravitee-lab for next
# time. In a terminal (no sudo: it asks when a step needs it):
#
#   curl -fsSL https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.sh | sh
#
# It uses what the machine has and adds only what is missing: the tools the lab needs (on macOS, Apple's command line tools;
# on Linux, from the package manager, after asking), the clone in ~/gravitee-lab and its settings, the command gravitee-lab in
# ~/.local/bin and, when that folder is not on the PATH, the line of your shell's profile that puts it there. Then it opens the
# Lab UI, which checks Docker and says how to install or start it. It never deletes anything, and can run again at any time.
#
# GRAVITEE_LAB_DIR names the folder of the clone, GRAVITEE_LAB_REPOSITORY and GRAVITEE_LAB_BRANCH what to clone (a fork, a
# branch), GRAVITEE_LAB_YES=1 answers yes to its questions (with no terminal to ask in), and GRAVITEE_LAB_NO_START=1 stops
# before the Lab UI. NO_COLOR=1 turns the colors off. On Windows, use install.ps1.
#
# Everything is in functions that run on the last line: a download cut halfway runs nothing.

help=https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md
marker='# Added by Gravitee Lab'

# The look. In a terminal, a step shows a live line while it works: a spinner, what it does now (the last line its tool
# wrote, or a bar when that line has a percentage) and the time, then one line with its outcome. Elsewhere (a log, CI), only
# the outcome lines. ASCII when the terminal is not UTF-8, no color with NO_COLOR
looks() {
    live=false
    [ -t 1 ] && [ "${TERM:-dumb}" != dumb ] && live=true
    e=$(printf '\033')
    if $live; then
        clear="${e}[2K" hide="${e}[?25l" show="${e}[?25h"
    else
        clear='' hide='' show=''
    fi
    if $live && [ -z "${NO_COLOR:-}" ]; then
        bold="${e}[1m" dim="${e}[2m" red="${e}[31m" green="${e}[32m" yellow="${e}[33m" cyan="${e}[36m" off="${e}[0m"
    else
        bold='' dim='' red='' green='' yellow='' cyan='' off=''
    fi
    case ${LC_ALL:-${LC_CTYPE:-${LANG:-}}} in
        *[Uu][Tt][Ff]-8* | *[Uu][Tt][Ff]8*)
            frames='⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏' good='✔' warn='▲' bad='✖' ask_mark='?'
            if [ -n "$cyan" ]; then full='━' empty='━'; else full='█' empty='░'; fi
            ;;
        *)
            # shellcheck disable=SC2089 # the backslash is a frame
            frames='- \ | /' good='+' warn='!' bad='x' ask_mark='?' full='#' empty='-'
            ;;
    esac
    tick=0.1
    sleep 0.1 2> /dev/null || tick=1
    parent=$$ spinner='' label=''
    if ! notes=$(mktemp) || ! out=$(mktemp); then
        echo 'mktemp failed: is /tmp writable?' >&2
        exit 1
    fi
}

# The width of the terminal, read again as it can change
width() {
    cols=$({ stty size < /dev/tty; } 2> /dev/null) && cols=${cols#* }
    case $cols in '' | *[!0-9]* | 0) cols=$(tput cols 2> /dev/null) ;; esac
    case $cols in '' | *[!0-9]* | 0) cols=80 ;; esac
    echo "$cols"
}

# Seconds since $1, as people read them
took() {
    s=$(($(date +%s) - $1))
    if [ $s -ge 60 ]; then printf '%dm %02ds' $((s / 60)) $((s % 60)); else printf '%ds' $s; fi
}

# The last line the step's tool wrote, or its last note, without colors and control characters. A tool that redraws a line
# (git's progress) ends each version of it with a carriage return
latest() {
    from=$out
    [ -s "$out" ] || from=$notes
    tail -c 400 "$from" 2> /dev/null | LC_ALL=C tr '\r\t' '\n ' | LC_ALL=C sed "s/$e\[[0-9;?]*[A-Za-z]//g" \
        | LC_ALL=C tr -d '\000-\010\013-\037' | sed 's/^ *//' | grep -v '^ *$' | tail -n 1
}

# What the live line says after the spinner and the step: a bar with what it measures, or the latest line; then the time
detail() {
    time=$(took "$started")
    room=$(($1 - 22 - ${#time}))
    text=$(latest)
    percent=$(printf '%s\n' "$text" | LC_ALL=C sed -n 's/.*[^0-9]\([0-9]\{1,3\}\)%.*/\1/p')
    if [ -n "$percent" ] && [ "$percent" -le 100 ] && [ $room -ge 30 ]; then
        size=24
        [ $room -ge 60 ] || size=10
        what=$(printf '%s\n' "$text" | LC_ALL=C sed -e 's/^remote: *//' -e 's/:* *[0-9]*%.*//')
        rate=$(printf '%s\n' "$text" | LC_ALL=C sed -n 's/.*), \(.*\/s\)$/\1/p' | sed 's/ | /, /')
        filled=$((percent * size / 100)) i=0 done_part='' rest_part=''
        while [ $i -lt $size ]; do
            if [ $i -lt $filled ]; then done_part="$done_part$full"; else rest_part="$rest_part$empty"; fi
            i=$((i + 1))
        done
        text=$(printf '%3s%%  %s%s' "$percent" "$what" "${rate:+, $rate}" | LC_ALL=C cut -c "1-$((room - size - 1))")
        printf '%s%s%s%s%s %s  %s%s%s' "$cyan" "$done_part" "$off$dim" "$rest_part" "$off" "$text" "$dim" "$time" "$off"
    else
        [ $room -ge 10 ] || room=10
        printf '%s  %s%s%s' "$(printf '%s' "$text" | LC_ALL=C cut -c "1-$room")" "$dim" "$time" "$off"
    fi
}

# The live line, redrawn in the background until the step ends (or this script, if it is killed)
spin() {
    trap - EXIT INT
    trap 'exit 0' TERM
    # shellcheck disable=SC2086,SC2090 # one word per frame
    set -- $frames
    n=0
    while kill -0 "$parent" 2> /dev/null; do
        [ $((n % 3)) != 0 ] || line=$(detail "$(width)")
        printf '\r%s  %s%s%s %s%-13s%s %s' "$clear" "$cyan" "$1" "$off" "$bold" "$label" "$off" "$line"
        frame=$1
        shift
        set -- "$@" "$frame"
        n=$((n + 1))
        sleep "$tick"
    done
}

resume() {
    if $live && [ -z "$spinner" ] && [ -n "$label" ]; then
        spin &
        spinner=$!
    fi
}

calm() {
    if [ -n "$spinner" ]; then
        kill "$spinner" 2> /dev/null
        wait "$spinner" 2> /dev/null
        spinner=''
        printf '\r%s' "$clear"
    fi
}

# A step: its name, and what it does first
step() {
    label=$1 started=$(date +%s)
    : > "$out"
    printf '%s\n' "$2" > "$notes"
    resume
}

# What the step does now: on the live line, or on a line of its own when there is none
note() {
    printf '%s\n' "$1" >> "$notes"
    $live || printf '    %s\n' "$1"
}

# A command of the step, its output on the live line and kept for the message of a failure
quiet() {
    "$@" >> "$out" 2>&1
}

# The outcome of the step: its mark, its name, what it found or did, and its time when that was long enough to notice
ended() {
    calm
    time=$(took "$started")
    case $time in 0s | 1s) time='' ;; esac
    printf '  %s%s%s %s%-13s%s %s%s%s%s\n' "$2" "$1" "$off" "$bold" "$label" "$off" "$3" "${time:+  }" "$dim" "$time$off"
    label=''
}
passed() { ended "$good" "$green" "$1"; }
warned() { ended "$warn" "$yellow" "$1"; }

# A paragraph, folded to the terminal and indented
paragraph() {
    printf '%s\n' "$2" | fold -s -w $(($(width) - 4)) | sed "s/^/  $1/;s/\$/$off/"
}

# The end: the step that failed with the last lines of its tool, what to do, and where to read more
halt() {
    if [ -n "$label" ]; then
        ended "$bad" "$red" ''
        if [ -s "$out" ]; then
            printf '\n'
            tail -n 6 "$out" | LC_ALL=C tr '\r' '\n' | grep -v '^ *$' | tail -n 6 | LC_ALL=C cut -c "1-$(($(width) - 6))" \
                | sed "s/^/    $dim/;s/\$/$off/"
        fi
    fi
    printf '\n'
    paragraph "$red" "$1"
    printf '  %sHelp: %s%s%s\n\n' "$dim" "$help" "${2:-}" "$off"
    exit 1
}

# A question, answered from the terminal: the script itself is what `curl | sh` gives as input. Yes with GRAVITEE_LAB_YES=1;
# no when there is no terminal to ask in
ask() {
    [ "${GRAVITEE_LAB_YES:-}" = 1 ] && return 0
    { true < /dev/tty; } 2> /dev/null || return 1
    calm
    printf '  %s%s%s %s %s(Y/n)%s %s' "$cyan" "$ask_mark" "$off" "$1" "$dim" "$off" "$show"
    read -r answer < /dev/tty || answer=n
    printf '%s' "$hide"
    case $answer in
        '' | [yY]*) resume ;;
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

# The tools found, with their versions
versions() {
    python_ok
    printf 'Git %s, Make %s, Python %s, %s' "$(git --version | awk '{print $3}')" \
        "$(make --version 2> /dev/null | awk 'NR == 1 {print $NF}')" \
        "$("$python" -c 'import platform; print(platform.python_version())')" "$(openssl version | awk '{print $1, $2}')"
}

# macOS: Apple's command line tools bring git, make and python3, and the system has openssl. Without them, their commands are
# stubs that open the install window. The window is opened once, brought to the front (it opens behind the terminal), and
# followed: the tools once they are there, a Cancel or a failed install (a full disk) as soon as the window closes
mac_tools() {
    step Tools 'Looking for Git, Make, Python and OpenSSL'
    if ! xcode-select -p > /dev/null 2>&1; then
        installer='/System/Library/CoreServices/Install Command Line Developer Tools.app'
        xcode-select --install > /dev/null 2>&1
        note "In the window of macOS, click Install, then Agree: its command line tools, about 1 GB"
        seen=0
        waited=0
        until xcode-select -p > /dev/null 2>&1; do
            if pgrep -f "$installer" > /dev/null 2>&1; then
                seen=$((seen + 1))
                # Its window opens behind the terminal: brought forward once it is drawn
                [ $seen != 2 ] || open -a "$installer" 2> /dev/null
            elif [ $seen -gt 0 ] || [ $waited -ge 30 ]; then
                sleep 3   # the tools are written just before the window closes
                xcode-select -p > /dev/null 2>&1 && break
                halt "The command line tools are not installed: their window was closed before the end, or macOS could not install them (they need a few GB of free disk). Run this command again to try again." '#macos'
            fi
            sleep 2
            waited=$((waited + 2))
        done
    fi
    gone=$(missing)
    [ -z "$gone" ] || halt "The command line tools are installed and ${gone}is still missing. Run: sudo xcode-select --reset, then run this command again." '#macos'
    passed "$(versions)"
}

# Linux: what is missing, from the package manager, as root through sudo. Each manager has its own name for Python
linux_tools() {
    step Tools 'Looking for Git, Make, Python and OpenSSL'
    gone=$(missing)
    if [ -z "$gone" ]; then
        passed "$(versions)"
        return
    fi
    packages=$gone
    # A system whose python3 is older than 3.9 ships a later one under another name: the first its manager has is installed
    newer=''
    if command -v apt-get > /dev/null 2>&1; then
        install='apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y'
        newer='python3.12 python3.11 python3.10 python3.9'
    elif command -v dnf > /dev/null 2>&1; then
        install='dnf install -y'
        newer='python3.12 python3.11 python39'
    elif command -v yum > /dev/null 2>&1; then
        install='yum install -y'
        newer='python3.11 python39'
    elif command -v zypper > /dev/null 2>&1; then
        install='zypper --non-interactive install'
        newer='python313 python312 python311 python310 python39'
    elif command -v pacman > /dev/null 2>&1; then
        install='pacman -S --needed --noconfirm'
        # A system that never synced its databases (a new container) syncs them first, and upgrades nothing
        ls /var/lib/pacman/sync/*.db > /dev/null 2>&1 || install="pacman -Sy --noconfirm && $install"
        packages=$(echo "$gone" | sed 's/python3/python/')
    elif command -v apk > /dev/null 2>&1; then
        install='apk add'
    else
        halt "Install ${gone}with the package manager of this system, then run this command again." '#linux'
    fi
    manager=${install##*&& }
    manager=${manager%% *}
    if [ "$(id -u)" = 0 ]; then
        root=''
    elif command -v sudo > /dev/null 2>&1; then
        root='sudo'
    else
        halt "${gone}is missing and sudo is not here to install it. Install ${gone}as root, then run this command again." '#linux'
    fi
    ask "Install $(echo "$packages" | sed 's/ $//;s/ /, /g') with $manager?" \
        || halt "Install ${packages}with $manager, then run this command again." '#linux'
    if [ -n "$root" ] && ! sudo -n true 2> /dev/null; then
        calm
        printf '%s' "$show"
        # shellcheck disable=SC2024 # sudo reads the password from the terminal
        sudo -v -p "  $cyan$ask_mark$off Password of %p for sudo: " < /dev/tty \
            || halt "sudo did not take the password. Install ${packages}with $manager, then run this command again." '#linux'
        printf '%s' "$hide"
        # A sudo that keeps no password (timestamp_timeout=0) asks again at each command: no live line hides its question
        if sudo -n true 2> /dev/null; then resume; else live=false; fi
    fi
    note "Installing $(echo "$packages" | sed 's/ $//;s/ /, /g') with $manager"
    # shellcheck disable=SC2086
    quiet $root sh -c "$install $packages" \
        || halt "${packages}did not install: $manager says why above. Is the internet reachable from this machine?" '#linux'
    if ! python_ok; then
        note 'The python3 of this system is older than 3.9: adding a later one beside it'
        for package in $newer; do
            # shellcheck disable=SC2086
            quiet $root sh -c "$install $package" && python_ok && break
        done
    fi
    python_ok || halt "Python 3.9 or later is missing, and the package manager of this system has none: install one, then run this command again." '#linux'
    gone=$(missing)
    [ -z "$gone" ] || halt "${gone}is still missing: install it, then run this command again." '#linux'
    passed "$(versions)"
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
    step 'Gravitee Lab' "Cloning into $(shown "$lab")"
    if [ -e "$lab" ]; then
        if [ -f "$lab/lab-ui/server.py" ] && [ -f "$lab/bin/gravitee-lab" ] && git -C "$lab" rev-parse --git-dir > /dev/null 2>&1; then
            passed "$(shown "$lab"), already there"
            return
        fi
        if [ -f "$lab/lab-ui/server.py" ]; then
            halt "$(shown "$lab") holds a Gravitee Lab older than the command gravitee-lab. Update it (cd $(shown "$lab") && make update), then run this command again."
        fi
        halt "$(shown "$lab") holds something else. Move it, or name another folder with GRAVITEE_LAB_DIR, then run this command again."
    fi
    quiet git clone --progress --branch "$branch" "$repository" "$lab" \
        || halt "The clone failed: git says why above. Behind a proxy that inspects HTTPS, git needs the certificate of your company (Corporate proxy, in docs/troubleshooting.md)." '#prerequisites'
    passed "$(shown "$lab")"
}

# What a fresh clone lacks (make init), made here so that the Lab UI starts with nothing to say
settings() {
    step Settings 'Settings and demo certificates'
    quiet make -C "$lab" --no-print-directory init \
        || halt "The settings of the lab could not be made: make init says why above." '#run-it'
    passed '.env and demo certificates'
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
    step Command "Linking gravitee-lab into ~/.local/bin"
    mkdir -p "$HOME/.local/bin" || halt "$HOME/.local/bin cannot be made."
    if [ -e "$HOME/.local/bin/gravitee-lab" ] && [ ! -L "$HOME/.local/bin/gravitee-lab" ]; then
        halt "$HOME/.local/bin/gravitee-lab is a file of something else. Move it, then run this command again."
    fi
    ln -sf "$lab/bin/gravitee-lab" "$HOME/.local/bin/gravitee-lab"
    case :$PATH: in
        *:"$HOME/.local/bin":*)
            passed 'gravitee-lab'
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
    fresh=' in a new terminal'
    passed "gravitee-lab, in new terminals ($(shown "$file") puts ~/.local/bin on the PATH)"
}

# Docker's command: on the PATH, or where Docker Desktop puts it when a terminal opened before it was installed does not
# find it yet (the Lab UI looks in the same places)
docker_cli() {
    for docker in "$(command -v docker 2> /dev/null)" /usr/local/bin/docker "$HOME/.docker/bin/docker" \
        /Applications/Docker.app/Contents/Resources/bin/docker; do
        [ -n "$docker" ] && [ -x "$docker" ] && echo "$docker" && return
    done
}

# Docker is only looked at: the Lab UI checks it with the rest of the machine, and says how to install or start it
docker_state() {
    step Docker 'Looking for Docker'
    docker=$(docker_cli)
    if [ -z "$docker" ]; then
        warned 'Not installed: the Lab UI says how to get it'
    elif version=$("$docker" version --format '{{.Server.Version}}' 2> /dev/null) && [ -n "$version" ]; then
        passed "Docker $version is running"
    else
        warned 'Installed, not running: the Lab UI starts it, or says how'
    fi
}

# The machine, as its owner names it
machine() {
    if [ "$system" = macOS ]; then
        printf 'macOS %s' "$(sw_vers -productVersion 2> /dev/null)"
    else
        # shellcheck disable=SC1091 # the file of the system that runs this
        (. /etc/os-release 2> /dev/null && printf '%s' "${PRETTY_NAME:-Linux}") || printf 'Linux'
        grep -qi microsoft /proc/version 2> /dev/null && printf ' on WSL'
    fi
    printf ', %s' "$(uname -m)"
}

finish() {
    calm
    printf '%s' "$show"
    rm -f "$notes" "$out"
}

main() {
    looks
    trap finish EXIT
    trap 'finish; printf "\n\n  Stopped. Run the command again to go on: what is done stays done.\n\n"; exit 130' INT TERM
    printf '%s' "$hide"
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
    begun=$(date +%s) fresh=''

    printf '\n  %sGravitee Lab%s  %s%s%s\n\n' "$bold" "$off" "$dim" "$(machine)" "$off"
    if [ "$system" = macOS ]; then mac_tools; else linux_tools; fi
    clone
    settings
    command_on_path
    docker_state

    printf '\n  %s%s Ready in %s.%s Next time, run %sgravitee-lab%s%s.\n' "$green" "$good" "$(took "$begun")" "$off" "$bold" "$off" "$fresh"
    if [ "${GRAVITEE_LAB_NO_START:-}" = 1 ]; then
        printf '\n'
        return
    fi
    printf '\n  Opening the Lab UI in your browser. Keep this terminal open while you use it.\n\n'
    finish
    trap - EXIT INT TERM
    exec "$lab/bin/gravitee-lab" < /dev/null
}

main "$@"
